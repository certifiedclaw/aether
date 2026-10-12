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
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

from tools.maintenance import REPO_ROOT, MAINT_DATA_DIR, write_if_changed

REQUIREMENTS_FILE = REPO_ROOT / "requirements.txt"
TIMEOUT = 8
USER_AGENT = "aether-maintenance-toolkit/1.0"

# Non-pip infra that Aether depends on but isn't in requirements.txt.
# (repo, human label, why it matters)
GITHUB_WATCHLIST = [
    ("ollama/ollama", "Ollama", "local LLM runtime — core/llm_client.py talks to it"),
    ("SYSTRAN/faster-whisper", "faster-whisper (engine)", "default STT engine per requirements.txt"),
    ("hexgrad/Kokoro-82M", "Kokoro TTS", "offline TTS option, alternative to edge-tts"),
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


def _fetch_json(url: str) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None


def _pypi_latest(package: str) -> str | None:
    data = _fetch_json(f"https://pypi.org/pypi/{package}/json")
    if not data:
        return None
    return data.get("info", {}).get("version")


def _github_latest_release(repo: str) -> str | None:
    data = _fetch_json(f"https://api.github.com/repos/{repo}/releases/latest")
    if data and "tag_name" in data:
        return data["tag_name"]
    # Fall back to tags if no formal "release" exists
    data = _fetch_json(f"https://api.github.com/repos/{repo}/tags")
    if data and isinstance(data, list) and data:
        return data[0].get("name")
    return None


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
    for repo, label, why in GITHUB_WATCHLIST:
        latest = _github_latest_release(repo)
        infra_results.append({
            "repo": repo, "label": label, "why": why,
            "latest": latest or "unknown (lookup failed)",
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
    lines.append("| Component | Latest | Why it matters |")
    lines.append("|---|---|---|")
    for item in data["infra"]:
        lines.append(f"| [{item['label']}](https://github.com/{item['repo']}) | {item['latest']} | {item['why']} |")
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
