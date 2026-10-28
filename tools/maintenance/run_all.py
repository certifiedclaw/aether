"""
Runs every maintenance check and regenerates all reports in one shot:

    PROJECT_STATE.md      (tools.maintenance.project_state)
    HEALTH_REPORT.md       (tools.maintenance.health_check)
    DEPENDENCY_REPORT.md   (tools.maintenance.dep_watcher — needs internet)
    ROADMAP.md              (tools.maintenance.roadmap render)

Then writes MAINTENANCE.md at the repo root as a one-page index into all
four, so there's a single obvious file to open.

Usage:
    python -m tools.maintenance.run_all
    python -m tools.maintenance.run_all --skip-deps   # skip network call
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from tools.maintenance import REPO_ROOT, write_if_changed
from tools.maintenance import project_state, health_check, dep_watcher, roadmap, handoff, publish


def main():
    parser = argparse.ArgumentParser(description="Run all Aether maintenance checks")
    parser.add_argument("--skip-deps", action="store_true", help="Skip the dependency watcher (no internet needed)")
    parser.add_argument("--publish", action="store_true", help="Commit (and push) the refreshed reports to git afterward")
    parser.add_argument("--no-push", action="store_true", help="With --publish, commit locally only, don't push")
    args = parser.parse_args()

    print("Running project_state...")
    project_state.main()

    print("Running health_check...")
    health_check.main()

    dep_status = "skipped"
    if not args.skip_deps:
        print("Running dep_watcher (requires internet)...")
        try:
            dep_watcher.main()
            dep_status = "ok"
        except Exception as e:
            print(f"  dep_watcher failed ({e}) — continuing without it.")
            dep_status = f"failed: {e}"
    else:
        print("Skipping dep_watcher (--skip-deps).")

    print("Rendering roadmap...")
    roadmap.cmd_render(argparse.Namespace())

    print("Rendering handoff log...")
    handoff.cmd_render(argparse.Namespace())

    index = f"""# Aether — Maintenance Index

> **Auto-generated.** Rerun `python -m tools.maintenance.run_all` to refresh
> everything. This is the front door — start here.

Last full run: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
Dependency check: {dep_status}

| Report | What it tells you |
|---|---|
| [`HANDOFF.md`](HANDOFF.md) | What previous sessions did and what's next — **read this first**, especially if work spans multiple LLM instances with no shared memory |
| [`PROJECT_STATE.md`](PROJECT_STATE.md) | Current module layout, sizes, entry points, recent git history |
| [`HEALTH_REPORT.md`](HEALTH_REPORT.md) | TODOs, oversized files/functions, bare excepts, stray prints, syntax errors |
| [`DEPENDENCY_REPORT.md`](DEPENDENCY_REPORT.md) | Latest versions of pip packages + Ollama/faster-whisper/Kokoro upstream |
| [`ROADMAP.md`](ROADMAP.md) | Hand-curated backlog — edit via `python -m tools.maintenance.roadmap` |

Also in this repo:
- [`AGENTS.md`](AGENTS.md) — onboarding for any AI assistant, not just Claude
- [`CLAUDE.md`](CLAUDE.md) — auto-read by Claude Code at session start
- [`docs/PROJECT_INSTRUCTIONS.md`](docs/PROJECT_INSTRUCTIONS.md) — the exact text to paste into a Claude Project's custom instructions

## Before you finish a session
Add a handoff entry — the next session (possibly a different LLM entirely) depends on it:
```
python -m tools.maintenance.handoff add --by "<model/tool>" --summary "..." --files "..." --next "..."
```

## Recommended cadence
Run this at the start of a work session (or hand a fresh Aether zip to
Claude and ask it to run this first) so both you and Claude are looking at
current state instead of a stale IMPROVEMENTS_V*.md snapshot.
"""
    if write_if_changed(REPO_ROOT / "MAINTENANCE.md", index):
        print(f"Wrote {REPO_ROOT / 'MAINTENANCE.md'}")
    else:
        print(f"{REPO_ROOT / 'MAINTENANCE.md'} unchanged")

    if args.publish:
        print("\nPublishing reports to git...")
        ok, status = publish.commit_and_push(push=not args.no_push)
        print(status)

    print("\nDone. Start at MAINTENANCE.md.")


if __name__ == "__main__":
    main()
