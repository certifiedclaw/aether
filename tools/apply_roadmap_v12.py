"""Add the v12 backlog items through the project's own roadmap code (never hand-edit).

Idempotent: an item whose title is already on the roadmap is skipped, so it is safe to
run twice and safe if you already added some of these yourself.

    python tools\\apply_roadmap_v12.py          (from the repo root)

It does NOT mark anything done: #23 (the desktop exec sandbox) stays open until you have
verified the fixes on your machine, then:  python -m tools.maintenance.roadmap done 23
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.maintenance import roadmap  # noqa: E402

# (priority, title, notes)
PLANNED = [
    ("high",
     "Approve a whole computer_control/browser_control task once, instead of only logging each action",
     "core/risk.py gives these tools the MONITOR tier: UI automation can drive any program, but a "
     "dialog per click would make the assistant unusable. Needs a per-task approval that names the goal."),
    ("medium",
     "Egress log: record outbound destinations (search queries, URLs, message recipients) as events",
     "Goose egress_inspector idea; observational only. Builds on the MONITOR-tier log lines in core/risk.py."),
    ("medium",
     "Owner enable/disable per tool, separate from installed/available",
     "Leon tool-state model; also covers 'skills default off'. risk_overrides can already tighten a tool."),
    ("medium",
     "Non-blocking confirmation banner: tool returns at once, the model tells the user to confirm on screen",
     "Mark-LV core/confirm.py pattern. confirm_blocking holds the worker thread for up to 120s with no "
     "spoken prompt. Keep fail-closed on timeout."),
    ("medium",
     "Central get_secret(): env > OS keyring > config file",
     "PersonalJarvis core/config.py pattern. Only 3 sites consume a secret (llm_client x2 for Gemini, tts for "
     "ElevenLabs); the other readers of api_keys.json read non-secret settings. Built as core/credentials.py + "
     "tools/secrets_cli.py: opt-in, the file stays the fallback. Close after verifying on Windows."),
    ("medium",
     "Execution timeout for AI-generated desktop code",
     "exec() cannot be killed in-process, so approved code with an infinite loop hangs its thread. "
     "Run it in a worker with a deadline."),
    ("medium",
     "Review MONITOR-tier tools that can send data to a cloud model (screen_process, clipboard_read, translate)",
     "Consider ASK when a cloud model (Gemini escalation) is active; local-only can stay MONITOR."),
    ("low",
     "UI-only way to loosen a risk tier (anti-confirmation-fatigue)",
     "risk_overrides is tighten-only on purpose: the model can write files, so a loosenable setting in a "
     "writable config would let it switch its own gates off. Loosening needs to come from the UI."),
    ("low",
     "Deterministic shell-command impact check for dev_agent's run_command dialog",
     "PersonalJarvis command_impact.py / Leon shell-tool idea. _run_project has no shell, so chaining is "
     "not possible; this is a clearer warning for unusual executables, not a new gate."),
    ("low",
     "Measure memory recall quality on a larger set than the 24-case replay",
     "The replay's single miss was the 240x17 turn, which needs no memory, so it says nothing either way."),
    ("low",
     "Open Interpreter integration targets the old Python package; the main repo is now a Rust Codex fork",
     "Check what pip install open-interpreter provides before relying on integrations/open_interpreter; "
     "consider MCP/ACP delegation instead."),
    ("low",
     "Fix dep_watcher lookups for Ollama, faster-whisper and Kokoro",
     "DEPENDENCY_REPORT.md showed 'lookup failed' for all three upstream checks."),
    ("low",
     "Investigate long_term.json identity entries missing 'value'",
     "Handoff guessed user redaction; unconfirmed."),
]


# Items first added under a different title (wording corrected later): if the OLD title is
# already on the roadmap, the new one is treated as present, so nobody gets a duplicate.
RENAMED = {
    "Central get_secret(): env > OS keyring > config file":
        "Central get_secret(): env > OS keyring > api_keys.json; migrate the 8+ direct file readers",
}


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def apply() -> tuple[list[int], list[str]]:
    """Add the missing items. Returns (new ids, titles skipped as already present)."""
    existing = {_norm(i["title"]) for i in roadmap._load()["items"]}
    added: list[int] = []
    skipped: list[str] = []
    for priority, title, notes in PLANNED:
        if _norm(title) in existing or (title in RENAMED and _norm(RENAMED[title]) in existing):
            skipped.append(title)
            continue
        before = roadmap._load()["next_id"]
        roadmap.cmd_add(argparse.Namespace(title=title, priority=priority, notes=notes))
        added.append(before)
        existing.add(_norm(title))
    return added, skipped


def main() -> int:
    added, skipped = apply()
    roadmap.cmd_render(argparse.Namespace())
    print(f"\nAdded {len(added)} item(s); skipped {len(skipped)} already present.")
    for t in skipped:
        print(f"  already there: {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
