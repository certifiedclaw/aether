"""core/doctor.py -- `/doctor`: find what is wrong with an Aether install and say how to fix it.

    /doctor          report (chat)                 python -m core.doctor [--json]
    /doctor fix      also apply the SAFE auto-fixes  python -m core.doctor --fix

Every check returns a Finding with a status (OK / WARN / FAIL), what it saw, and what to do.
Auto-fixes are deliberately tiny and reversible: create a missing folder, tighten a secrets
file's permissions. Nothing that edits your config values, installs software, deletes data or
touches the network is ever applied automatically -- those are only described.

Secrets are never printed: only where a key comes from (env / keyring / file), never its value.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OK, WARN, FAIL = "OK", "WARN", "FAIL"
_ORDER = {FAIL: 0, WARN: 1, OK: 2}
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
_DEFAULT_OLLAMA = "http://localhost:11434"


@dataclass
class Finding:
    name: str
    status: str
    detail: str
    fix: str = ""          # what the user should do
    auto: str = ""         # key into AUTO_FIXES if `/doctor fix` can do it safely


# ── probes (module-level so tests can replace them) ──────────────────────────────
def _http_json(url: str, timeout: float = 4.0):
    """GET `url`, return parsed JSON or None on any failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def _ram() -> tuple[float, float] | None:
    """(available_gb, total_gb) or None when psutil is missing."""
    try:
        import psutil
        vm = psutil.virtual_memory()
        return vm.available / 2**30, vm.total / 2**30
    except Exception:  # noqa: BLE001
        return None


def _git(root: Path, *args: str) -> int | None:
    if not (root / ".git").exists() or not shutil.which("git"):
        return None
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=10).returncode
    except Exception:  # noqa: BLE001
        return None


# ── config loading (never raises) ───────────────────────────────────────────────
def _load_cfg(root: Path) -> tuple[dict, Finding]:
    from core import config_home
    p = config_home.config_path(root)
    if not p.exists():
        return {}, Finding("Config file", WARN, f"{p} is missing; built-in defaults are in use.",
                           "Copy it from your previous Aether folder, or from config/config.example.json.")
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise ValueError("not a JSON object")
        return d, Finding("Config file", OK, f"{p} is valid.")
    except Exception as e:  # noqa: BLE001
        bak = p.with_name(p.name + ".corrupt")
        good = config_home.last_good_path(p)
        if good.exists():
            return config_home.load_config(p), Finding("Config file", WARN, f"{p} cannot be read ({type(e).__name__}); Aether is using the last good copy ({good.name}).",
                               "Fix the JSON (a missing comma or quote is the usual cause); settings changes are ignored until you do.")
        extra = f" A backup of an earlier bad copy exists at {bak.name}." if bak.exists() else ""
        return {}, Finding("Config file", FAIL, f"{p} cannot be read ({type(e).__name__}).{extra}",
                           "Fix the JSON (a missing comma or quote is the usual cause) or restore your last copy.")


def _cfg_get(cfg: dict, dotted: str, default=None):
    if dotted in cfg:
        return cfg[dotted]
    cur = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


# ── checks ──────────────────────────────────────────────────────────────────────
def check_python() -> Finding:
    v = sys.version_info
    ok = v >= (3, 10)
    return Finding("Python", OK if ok else FAIL, f"{v.major}.{v.minor}.{v.micro}",
                   "" if ok else "Aether needs Python 3.10 or newer.")


def check_config_permissions(root: Path) -> Finding | None:
    from core import config_home
    p = config_home.config_path(root)
    if os.name != "posix" or not p.exists():
        return None
    mode = p.stat().st_mode & 0o777
    if mode & 0o077:
        return Finding("Config permissions", WARN, f"{p} is readable by other users (mode {mode:o}).",
                       f"chmod 600 {p}", auto="chmod_config")
    return Finding("Config permissions", OK, "only the owner can read the config file.")


def check_config_location(root: Path) -> Finding | None:
    """Tell the user where the live config is, and flag a stale old in-app copy."""
    from core import config_home
    if root != ROOT:
        return None
    live = config_home.config_path(root)
    old = config_home.stale_legacy_copy()
    if old is not None:
        return Finding("Config location", WARN,
                       f"live settings are in {live}, but an older, different copy remains at {old}; the old one is ignored.",
                       f"Edit {live} (or delete {old} once you are happy).")
    return Finding("Config location", OK, f"settings live in {live} and survive version upgrades.")


def check_secret_storage(root: Path, cfg: dict) -> Finding | None:
    try:
        from core import credentials as cred
        names = list(cred.SECRET_NAMES)
        in_file = [n for n in names if cred.source_of(n, cfg) == "file"]
    except Exception:  # noqa: BLE001
        return None
    if in_file:
        return Finding("Secret storage", WARN, "keys are stored in plain text in the config file: " + ", ".join(in_file),
                       "Move them to the OS keyring or environment: python -m tools.secrets_cli migrate")
    return Finding("Secret storage", OK, "no keys are stored in the config file.")


def check_git_secrets(root: Path) -> list[Finding]:
    out: list[Finding] = []
    tracked = _git(root, "ls-files", "--error-unmatch", "config/api_keys.json")
    if tracked == 0:
        out.append(Finding("Git secrets", FAIL, "config/api_keys.json is tracked by git, so it can be pushed.",
                           "git rm --cached config/api_keys.json, add it to .gitignore, and rotate every key in it."))
    elif tracked is not None:
        ignored = _git(root, "check-ignore", "-q", "config/api_keys.json")
        if ignored == 0:
            out.append(Finding("Git secrets", OK, "config/api_keys.json is not tracked and is ignored."))
        elif ignored is not None:
            out.append(Finding("Git secrets", WARN, "config/api_keys.json is not tracked but is not in .gitignore.",
                               "Add config/api_keys.json to .gitignore so `git add .` cannot pick it up."))
    return out


def check_dirs(root: Path) -> Finding:
    missing = [d for d in ("logs", "data", "memory") if not (root / d).is_dir()]
    unwritable = [d for d in ("logs", "data", "memory") if (root / d).is_dir() and not os.access(root / d, os.W_OK)]
    if unwritable:
        return Finding("Folders", FAIL, "not writable: " + ", ".join(unwritable),
                       "Fix the folder permissions or run Aether as the user that owns them.")
    if missing:
        return Finding("Folders", WARN, "missing: " + ", ".join(missing), "Created automatically on first use.",
                       auto="make_dirs")
    return Finding("Folders", OK, "logs, data and memory exist and are writable.")


def check_disk(root: Path) -> list[Finding]:
    out: list[Finding] = []
    try:
        free = shutil.disk_usage(root).free / 2**30
        st = OK if free >= 5 else WARN if free >= 1 else FAIL
        out.append(Finding("Disk space", st, f"{free:.1f} GB free.",
                           "" if st == OK else "Free some space; models, logs and memory all write here."))
    except Exception as e:  # noqa: BLE001
        out.append(Finding("Disk space", WARN, f"could not check ({type(e).__name__})."))
    logs = root / "logs"
    if logs.is_dir():
        size = sum(f.stat().st_size for f in logs.rglob("*") if f.is_file()) / 2**20
        if size > 500:
            out.append(Finding("Log size", WARN, f"logs/ is {size:.0f} MB.", "Delete old files in logs/ (verify snapshots, replays)."))
    return out


def check_memory_files(root: Path) -> list[Finding]:
    out: list[Finding] = []
    db = root / "data" / "memory.db"
    if db.exists():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
            try:
                res = con.execute("PRAGMA quick_check").fetchone()
            finally:
                con.close()
            ok = bool(res) and res[0] == "ok"
            out.append(Finding("Memory database", OK if ok else FAIL,
                               "integrity check passed." if ok else f"integrity check failed: {res}",
                               "" if ok else "Restore from a backup (/backup makes one) and keep the damaged file."))
        except Exception as e:  # noqa: BLE001
            out.append(Finding("Memory database", FAIL, f"cannot open data/memory.db ({type(e).__name__}).",
                               "Close other Aether processes; if it persists restore from backup."))
    lt = root / "memory" / "long_term.json"
    if lt.exists():
        try:
            json.loads(lt.read_text(encoding="utf-8") or "{}")
            out.append(Finding("Long-term memory file", OK, "valid JSON."))
        except Exception as e:  # noqa: BLE001
            out.append(Finding("Long-term memory file", FAIL, f"memory/long_term.json is not valid JSON ({type(e).__name__}).",
                               "Restore it from a backup; do not delete it unless you want to forget everything."))
    return out


def check_llm(cfg: dict) -> list[Finding]:
    out: list[Finding] = []
    provider = str(cfg.get("llm_provider", "ollama")).lower()
    if provider in ("gemini", "openai_api"):
        name = "gemini_api_key" if provider == "gemini" else "openai_api_key"
        try:
            from core import credentials as cred
            src = cred.source_of(name, cfg)
        except Exception:  # noqa: BLE001
            src = None
        out.append(Finding("LLM provider", OK if src else FAIL,
                           f"{provider}: key {'found (' + src + ')' if src else 'NOT set'}. Prompts leave this machine.",
                           "" if src else f"Set {name} (env, keyring or config)."))
        return out

    url = str(cfg.get("llm_url") or cfg.get("ollama_url") or _DEFAULT_OLLAMA).rstrip("/")
    tags = _http_json(url + "/api/tags")
    if tags is None:
        out.append(Finding("LLM backend", FAIL, f"cannot reach {provider} at {url}.",
                           "Start Ollama (or check the URL / firewall / Tailscale)."))
        return out
    models = {m.get("name", ""): m for m in tags.get("models", [])}
    out.append(Finding("LLM backend", OK, f"{url} reachable, {len(models)} model(s) installed."))

    try:
        from core.llm_client import _DEFAULTS
        default_model = _DEFAULTS.get("llm_model", "")
    except Exception:  # noqa: BLE001
        default_model = ""
    want = str(cfg.get("llm_model") or default_model)
    if want:
        names = set(models)
        if want in names or (":" not in want and f"{want}:latest" in names):
            out.append(Finding("Model", OK, f"'{want}' is installed."))
            ram = _ram()
            size = models.get(want) or models.get(f"{want}:latest") or {}
            gb = (size.get("size") or 0) / 2**30
            if ram and gb and gb > ram[0] * 0.8:
                out.append(Finding("Model fits memory", WARN,
                                   f"'{want}' is {gb:.1f} GB but only {ram[0]:.1f} GB RAM is free; it will swap and crawl.",
                                   "Close other apps/containers or choose a smaller model."))
        else:
            close = sorted(n for n in names if n.split(":")[0] == want.split(":")[0])
            fix = (f"Installed variants: {', '.join(close)}. Set llm_model to one of them."
                   if close else f"Run: ollama pull {want}")
            out.append(Finding("Model", FAIL, f"the model Aether will use, '{want}', is not installed.", fix))

    ps = _http_json(url + "/api/ps")
    for m in (ps or {}).get("models", []):
        size, vram = m.get("size", 0), m.get("size_vram", 0)
        if size and vram < size * 0.9:
            out.append(Finding("GPU offload", WARN,
                               f"{m.get('name')} is only {100 * vram / size:.0f}% on the GPU; the rest runs on the CPU.",
                               "Use a smaller model or free VRAM (close games / other models: ollama stop <name>)."))
    return out


def check_remote(cfg: dict) -> Finding | None:
    remote = str(cfg.get("llm_remote_url") or "").strip()
    if not remote:
        return None
    ok = _http_json(remote.rstrip("/") + "/api/tags", timeout=3) is not None
    return Finding("Remote PC (GPU)", OK if ok else WARN,
                   f"{remote} is {'reachable' if ok else 'not reachable'}.",
                   "" if ok else "Aether will fall back to the local model. Check the PC is on and Tailscale is up.")


def check_dashboard(root: Path, env: dict | None = None) -> Finding:
    env = os.environ if env is None else env
    host = (env.get("AETHER_DASHBOARD_HOST") or "127.0.0.1").strip().lower()
    from core import config_home
    certs = config_home.certs_dir(root)
    tls = (certs / "aether.key").exists() and (certs / "aether.crt").exists()
    if host in _LOOPBACK:
        return Finding("Dashboard exposure", OK, "bound to this machine only (127.0.0.1).")
    if tls:
        return Finding("Dashboard exposure", OK, f"reachable on {host} over TLS.")
    return Finding("Dashboard exposure", FAIL, f"AETHER_DASHBOARD_HOST={host} but there are no TLS certificates; the dashboard will refuse to start.",
                   "Add config/certs/aether.{key,crt}, or unset AETHER_DASHBOARD_HOST for local-only access.")


def check_plugins(root: Path, cfg: dict) -> Finding | None:
    folder = root / "plugins"
    # same rule as core/plugins.py: files starting with "_" (the template, __init__) are never loaded
    files = sorted(p for p in folder.glob("*.py") if not p.name.startswith("_")) if folder.is_dir() else []
    if not files:
        return None
    trusted = _cfg_get(cfg, "plugins.trusted_hashes", {})
    trusted = trusted if isinstance(trusted, dict) else {}
    pending = []
    for p in files:
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        if str(trusted.get(p.name, "")).lower() != digest:
            pending.append(f"{p.name} (sha256 {digest})")
    if pending:
        return Finding("Plugins", WARN, "not trusted, so not loaded: " + "; ".join(pending),
                       "Read the file, and if you trust it add its name and hash to plugins.trusted_hashes in the config.")
    return Finding("Plugins", OK, f"{len(files)} plugin(s), all match their trusted hash.")


def check_resources() -> Finding | None:
    ram = _ram()
    if not ram:
        return None
    avail, total = ram
    pct = 100 * (1 - avail / total)
    st = OK if pct < 85 else WARN
    return Finding("Memory (RAM)", st, f"{pct:.0f}% used, {avail:.1f} GB free of {total:.1f} GB.",
                   "" if st == OK else "RAM is nearly full: models will stall. Stop heavy containers or apps.")


# ── auto-fixes (safe + reversible only) ─────────────────────────────────────────
def _fix_make_dirs(root: Path) -> str:
    made = []
    for d in ("logs", "data", "memory"):
        if not (root / d).is_dir():
            (root / d).mkdir(parents=True, exist_ok=True)
            made.append(d)
    return "created folder(s): " + ", ".join(made) if made else "nothing to create"


def _fix_chmod_config(root: Path) -> str:
    from core import config_home
    p = config_home.config_path(root)
    os.chmod(p, 0o600)
    return f"set {p} to owner-only (600)"


AUTO_FIXES = {"make_dirs": _fix_make_dirs, "chmod_config": _fix_chmod_config}


def apply_fixes(findings: list[Finding], root: Path = ROOT) -> list[str]:
    done: list[str] = []
    for key in dict.fromkeys(f.auto for f in findings if f.auto and f.status != OK):
        try:
            done.append(AUTO_FIXES[key](root))
        except Exception as e:  # noqa: BLE001
            done.append(f"could not apply {key}: {type(e).__name__}: {e}")
    return done


# ── run + render ────────────────────────────────────────────────────────────────
def run_checks(root: Path = ROOT) -> list[Finding]:
    cfg, cfg_finding = _load_cfg(root)
    found: list[Finding] = [check_python(), cfg_finding]
    for item in (check_config_permissions(root), check_config_location(root), check_secret_storage(root, cfg)):
        if item:
            found.append(item)
    found += check_git_secrets(root)
    found.append(check_dirs(root))
    found += check_disk(root)
    found += check_memory_files(root)
    found += check_llm(cfg)
    for item in (check_remote(cfg), check_dashboard(root), check_plugins(root, cfg), check_resources()):
        if item:
            found.append(item)
    return found


def render(findings: list[Finding], fixed: list[str] | None = None) -> str:
    icon = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}
    lines = ["Aether doctor", ""]
    for f in sorted(findings, key=lambda x: _ORDER[x.status]):
        lines.append(f"{icon[f.status]} {f.name}: {f.detail}")
        if f.fix and f.status != OK:
            lines.append(f"       -> {f.fix}" + ("   (auto-fixable: /doctor fix)" if f.auto else ""))
    n = {s: sum(1 for f in findings if f.status == s) for s in (OK, WARN, FAIL)}
    lines += ["", f"{n[OK]} ok, {n[WARN]} warning(s), {n[FAIL]} failure(s)."]
    if fixed:
        lines += ["", "Applied:"] + [f"  - {x}" for x in fixed]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    findings = run_checks()
    fixed = apply_fixes(findings) if "--fix" in args else None
    if fixed:
        findings = run_checks()
    if "--json" in args:
        print(json.dumps({"findings": [asdict(f) for f in findings], "fixed": fixed or []}, indent=2))
    else:
        print(render(findings, fixed))
    return 1 if any(f.status == FAIL for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
