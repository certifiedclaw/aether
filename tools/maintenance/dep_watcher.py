"""
Checks Aether's dependencies against their upstream sources and writes
DEPENDENCY_REPORT.md. Two kinds of checks:

  1. PyPI packages from requirements.txt -> latest version via the PyPI
     JSON API (https://pypi.org/pypi/<name>/json). Stdlib `urllib` only,
     no extra install required to run this tool itself.
  2. A short hand-maintained list of non-pip infra Aether depends on
     (Ollama, faster-whisper models, Kokoro) -> latest release via the
     GitHub API, since these aren't in requirements.txt but matter a lot
     for compatibility.

Network is required. If a lookup fails (offline, rate-limited, package
renamed) it's reported as "unknown" rather than crashing the whole run.

Usage:
    python -m tools.maintenance.dep_watcher
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

from tools.maintenance import REPO_ROOT, MAINT_DATA_DIR, write_if_changed

REQUIREMENTS_FILE = REPO_ROOT / "requirements.txt"
TIMEOUT = 8
USER_AGENT = "aether-maintenance-toolkit/1.0"

# Non-pip infra that Aether depends on but isn't (all) in requirements.txt.
# (github repo, human label, why it matters, PyPI fallback package or None)
#
# Kokoro: the code lives in hexgrad/kokoro (releases + the `kokoro` PyPI
# package). hexgrad/Kokoro-82M is the Hugging Face *model* repo and has no
# GitHub releases or tags, so it can never answer a "latest release" query.
GITHUB_WATCHLIST = [
    ("ollama/ollama", "Ollama", "local LLM runtime — core/llm_client.py talks to it", None),
    ("SYSTRAN/faster-whisper", "faster-whisper (engine)", "default STT engine per requirements.txt", "faster-whisper"),
    ("hexgrad/kokoro", "Kokoro TTS", "offline TTS option, alternative to edge-tts", "kokoro"),
]

# Packages known to churn fast or conflict with each other — always worth a
# second look even if "latest" doesn't obviously look scary.
WATCH_NOTES = {
    "open-interpreter": "Depends on litellm; historically conflicts with langchain/llama-index pins. Consider a separate venv.",
    "llama-index": "Splits into many llama-index-* subpackages frequently; check changelog before bumping.",
    "langchain": "Fast-moving API; pin a version once Aether's LangChain tool wiring is stable.",
}


def _parse_requirements() -> list[str]:
    if not REQUIREMENTS_FILE.exists():
        return []
    names = []
    for raw in REQUIREMENTS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # strip inline comments like `faster-whisper   # Whisper (default)`
        line = line.split("#")[0].strip()
        # strip environment markers like `; sys_platform == "win32"`
        line = line.split(";")[0].strip()
        # strip extras like fastapi[standard] -> fastapi, keep extras noted separately
        base = re.split(r"[\[<>=~!]", line)[0].strip()
        if base:
            names.append(base)
    # de-dupe, keep order
    seen = set()
    out = []
    for n in names:
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def _github_token() -> str | None:
    """Optional token (raises the API limit from 60/h per IP to 5000/h)."""
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None


def _describe_error(exc: Exception) -> str:
    """Short, human-readable reason for a failed lookup (shown in the report)."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (403, 429):
            return f"HTTP {exc.code} (rate-limited or blocked)"
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"network error: {exc.reason}"
    if isinstance(exc, TimeoutError):
        return "timeout"
    return f"{type(exc).__name__}"


def _fetch_json_ex(url: str, headers: dict | None = None) -> tuple[object | None, str | None]:
    """GET a JSON document. Returns (data, None) or (None, reason)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        return None, _describe_error(e)


def _fetch_json(url: str) -> dict | None:
    data, _err = _fetch_json_ex(url)
    return data if isinstance(data, dict) else None


def _pypi_latest(package: str) -> str | None:
    data = _fetch_json(f"https://pypi.org/pypi/{package}/json")
    if not data:
        return None
    return data.get("info", {}).get("version")


_TAG_URL_RE = re.compile(r"/releases/tag/([^/?#]+)")


def _github_web_latest_tag(repo: str) -> tuple[str | None, str | None]:
    """Latest release tag via the public web redirect
    (github.com/<repo>/releases/latest -> .../releases/tag/<tag>).

    This is not the REST API, so it isn't subject to the 60-requests/hour
    unauthenticated API limit that was making the API lookups fail.
    """
    req = urllib.request.Request(
        f"https://github.com/{repo}/releases/latest",
        headers={"User-Agent": USER_AGENT}, method="HEAD",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            m = _TAG_URL_RE.search(resp.geturl())
            return (m.group(1), None) if m else (None, "no releases published")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, _describe_error(e)


def _github_latest_release(repo: str, pypi_fallback: str | None = None) -> tuple[str | None, str]:
    """Best-effort latest version for a GitHub project.

    Tries, in order: REST releases/latest (token-authenticated when
    GITHUB_TOKEN/GH_TOKEN is set), the public web redirect, REST tags, and
    finally PyPI when a package name is given. Returns (version, detail):
    detail is the source used on success, or the collected failure reasons.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    reasons: list[str] = []

    data, err = _fetch_json_ex(f"https://api.github.com/repos/{repo}/releases/latest", headers)
    if isinstance(data, dict) and data.get("tag_name"):
        return data["tag_name"], "GitHub release"
    reasons.append(f"API releases: {err or 'no tag_name'}")

    tag, err = _github_web_latest_tag(repo)
    if tag:
        return tag, "GitHub release (web)"
    reasons.append(f"web: {err}")

    data, err = _fetch_json_ex(f"https://api.github.com/repos/{repo}/tags", headers)
    if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("name"):
        return data[0]["name"], "GitHub tag"
    reasons.append(f"API tags: {err or 'no tags'}")

    if pypi_fallback:
        ver = _pypi_latest(pypi_fallback)
        if ver:
            return ver, f"PyPI {pypi_fallback}"
        reasons.append(f"PyPI {pypi_fallback}: lookup failed")

    return None, "; ".join(reasons)


def run_check() -> dict:
    packages = _parse_requirements()
    pip_results = []
    for pkg in packages:
        latest = _pypi_latest(pkg)
        pip_results.append({
            "package": pkg,
            "latest": latest or "unknown (lookup failed)",
            "note": WATCH_NOTES.get(pkg.lower()),
        })

    infra_results = []
    for repo, label, why, pypi_fallback in GITHUB_WATCHLIST:
        latest, detail = _github_latest_release(repo, pypi_fallback)
        infra_results.append({
            "repo": repo, "label": label, "why": why,
            "latest": latest or f"unknown (lookup failed: {detail})",
            "source": detail if latest else None,
        })

    return {
        "_meta": {"generated": datetime.now(timezone.utc).isoformat(), "pip_packages": len(packages)},
        "pip": pip_results,
        "infra": infra_results,
    }


def render_report(data: dict) -> str:
    lines = []
    lines.append("# Aether — Dependency Report")
    lines.append("")
    lines.append("> **Auto-generated.** Rerun `python -m tools.maintenance.dep_watcher` "
                  "(or `run_all`) to refresh. Requires internet access.")
    lines.append("")
    lines.append(f"Generated: {data['_meta']['generated']}")
    lines.append("")
    lines.append("Note: `requirements.txt` doesn't pin exact versions, so this can't flag "
                  "\"you're 3 versions behind\" — it reports the latest available so you can "
                  "decide whether to pin and bump.")
    lines.append("")

    lines.append("## Core infra (not in requirements.txt)")
    lines.append("")
    lines.append("| Component | Latest | Source | Why it matters |")
    lines.append("|---|---|---|---|")
    for item in data["infra"]:
        lines.append(
            f"| [{item['label']}](https://github.com/{item['repo']}) | {item['latest']} "
            f"| {item.get('source') or '—'} | {item['why']} |"
        )
    lines.append("")

    lines.append("## Python packages (from requirements.txt)")
    lines.append("")
    lines.append("| Package | Latest on PyPI | Notes |")
    lines.append("|---|---|---|")
    for item in data["pip"]:
        note = item["note"] or ""
        lines.append(f"| {item['package']} | {item['latest']} | {note} |")
    lines.append("")

    flagged = [i for i in data["pip"] if i["note"]]
    if flagged:
        lines.append("## Flagged for extra attention")
        for item in flagged:
            lines.append(f"- **{item['package']}** — {item['note']}")
        lines.append("")

    return "\n".join(lines)


def main():
    data = run_check()
    (MAINT_DATA_DIR / "dependency_report.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    report = render_report(data)
    out_path = REPO_ROOT / "DEPENDENCY_REPORT.md"
    if write_if_changed(out_path, report):
        print(f"Wrote {out_path}")
    else:
        print(f"{out_path} unchanged")


if __name__ == "__main__":
    main()
