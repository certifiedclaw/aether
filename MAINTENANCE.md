# Aether — Maintenance Index

> **Auto-generated.** Rerun `python -m tools.maintenance.run_all` to refresh
> everything. This is the front door — start here.

Last full run: 2026-10-13 02:10 UTC
Dependency check: skipped

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
- [`PROJECT_INSTRUCTIONS.md`](PROJECT_INSTRUCTIONS.md) — the exact text to paste into a Claude Project's custom instructions

## Before you finish a session
Add a handoff entry — the next session (possibly a different LLM entirely) depends on it:
```
python -m tools.maintenance.handoff add --by "<model/tool>" --summary "..." --files "..." --next "..."
```

## Recommended cadence
Run this at the start of a work session (or hand a fresh Aether zip to
Claude and ask it to run this first) so both you and Claude are looking at
current state instead of a stale IMPROVEMENTS_V*.md snapshot.
