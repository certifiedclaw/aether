"""
Checks Aether's dependencies against their upstream sources and writes
DEPENDENCY_REPORT.md. Two kinds of checks:

  1. PyPI packages from requirements.txt -> latest version via the PyPI
     JSON API (https://pypi.org/pypi/<name>/json). Stdlib `urllib` only,
     no extra install required to run this tool itself.
  2. A short hand-maintained list of non-pip infra Aether depends on
     (Ollama, faster-whisper, Kokoro) -> latest release. Each is tried in
     order until one answers: GitHub API releases/latest, GitHub API tags,
     the github.com/<repo>/releases/latest redirect (not API rate-limited),
     then PyPI for projects that ship there. Set GITHUB_TOKEN to lift the
     60-requests/hour anonymous API limit.

Network is required. If every lookup for an item fails (offline,
rate-limited, repo renamed) it's reported as "unknown (<reason>)" -- with
the actual HTTP status or error -- rather than crashing the whole run.

Usage:
    python -m tools.maintenance.dep_watcher
"""
from __future__ import annotations

import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from tools.maintenance import REPO_ROOT, MAINT_DATA_DIR, write_if_changed

REQUIREMENTS_FILE = REPO_ROOT / "requirements.txt"
TIMEOUT = 8
USER_AGENT = "aether-maintenance-toolkit/1.0"

# Non-pip infra that Aether depends on but isn't in requirements.txt.
# (repo, human label, why it matters, PyPI fallback name or None)
#
# Kokoro: hexgrad/Kokoro-82M is the Hugging Face *model* repo and has no
# GitHub releases; the inference library lives at hexgrad/kokoro and ships on
# PyPI as "kokoro". Ollama has no PyPI fallback on purpose -- the "ollama"
# package on PyPI is the Python client, not the server Aether talks to.
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


def _describe_error(exc: Exception) -> str:
    """Short, report-safe reason for a failed request."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", exc)
        return f"network error: {reason}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(exc, ValueError):
        return "unparseable response"
    return type(exc).__name__


def _headers(url: str) -> dict:
    headers = {"User-Agent": USER_AGENT}
    # Only ever send the token to the GitHub API host, never to other URLs.
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
        headers["Accept"] = "application/vnd.github+json"
    return headers


def _fetch_json(url: str) -> tuple[dict | list | None, str | None]:
    """GET url and parse JSON. Returns (data, None) or (None, reason)."""
    req = urllib.request.Request(url, headers=_headers(url))
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except (urllib.error.URLError, TimeoutError, socket.timeout, ValueError) as exc:
        return None, _describe_error(exc)


def _pypi_latest(package: str) -> tuple[str | None, str | None]:
    data, err = _fetch_json(f"https://pypi.org/pypi/{package}/json")
    if not isinstance(data, dict):
        return None, f"PyPI {err or 'no data'}"
    version = data.get("info", {}).get("version")
    return (version, None) if version else (None, "PyPI returned no version")


_TAG_RE = re.compile(r"/releases/tag/([^/?#]+)")


def _github_redirect_latest(repo: str) -> tuple[str | None, str | None]:
    """Resolve github.com/<repo>/releases/latest via its redirect.

    Not part of the REST API, so it isn't subject to the 60/hour anonymous
    limit that makes api.github.com return 403 on shared or busy networks.
    """
    url = f"https://github.com/{repo}/releases/latest"
    req = urllib.request.Request(url, method="HEAD", headers=_headers(url))
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            final = resp.geturl()
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        return None, f"releases page {_describe_error(exc)}"
    match = _TAG_RE.search(final or "")
    if match:
        return urllib.parse.unquote(match.group(1)), None
    return None, "releases page: no published release"


def _github_latest_release(repo: str, pypi_fallback: str | None = None) -> tuple[str | None, str | None]:
    """Latest version of an upstream project. Returns (version, None) on
    success or (None, "reason; reason; ...") listing why each source failed."""
    reasons: list[str] = []

    data, err = _fetch_json(f"https://api.github.com/repos/{repo}/releases/latest")
    if isinstance(data, dict) and data.get("tag_name"):
        return data["tag_name"], None
    reasons.append(f"API releases {err or 'no tag_name'}")

    data, err = _fetch_json(f"https://api.github.com/repos/{repo}/tags")
    if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("name"):
        return data[0]["name"], None
    reasons.append(f"API tags {err or 'empty'}")

    version, err = _github_redirect_latest(repo)
    if version:
        return version, None
    reasons.append(err or "releases page failed")

    if pypi_fallback:
        version, err = _pypi_latest(pypi_fallback)
        if version:
            return f"{version} (PyPI)", None
        reasons.append(err or "PyPI failed")

    return None, "; ".join(reasons)


def run_check() -> dict:
    packages = _parse_requirements()
    pip_results = []
    for pkg in packages:
        latest, err = _pypi_latest(pkg)
        pip_results.append({
            "package": pkg,
            "latest": latest or f"unknown (lookup failed: {err})",
            "note": WATCH_NOTES.get(pkg.lower()),
        })

    infra_results = []
    for repo, label, why, pypi_name in GITHUB_WATCHLIST:
        latest, err = _github_latest_release(repo, pypi_name)
        infra_results.append({
            "repo": repo, "label": label, "why": why,
            "latest": latest or f"unknown (lookup failed: {err})",
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
