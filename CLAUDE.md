# Aether

Local, privacy-first voice AI assistant (PyQt6 UI, Ollama LLM, Whisper STT, EdgeTTS/Kokoro TTS).

## Start here, every session

Work on this project spans multiple separate sessions/instances with **no shared memory between them** — sometimes not even the same LLM. Read `HANDOFF.md` first: it's a running log of what previous sessions actually did and what they left for you to do next.

Then read `MAINTENANCE.md` — the auto-generated index into current project state. It links to:

- `PROJECT_STATE.md` — module layout, sizes, entry points, recent real git history
- `HEALTH_REPORT.md` — TODOs, oversized files/functions, bare excepts, stray prints, syntax errors
- `DEPENDENCY_REPORT.md` — latest PyPI/upstream versions vs what's pinned
- `ROADMAP.md` — the actual backlog (edit via `python -m tools.maintenance.roadmap`, never by hand)

These are regenerated automatically on every app launch (see the maintenance hook in `main.py`'s `main()`) and are safe to trust as current — don't rely on `docs/IMPROVEMENTS_V2.md` through `V6.md` or `docs/CHANGES.md`, those are historical snapshots that stop being updated.

If it's been a while since Aether was last launched, or you're working from a fresh clone, run this before trusting the reports:

```
python -m tools.maintenance.run_all
```

Before you finish this session, log a handoff entry so the next session (Claude or otherwise) isn't starting cold:

```
python -m tools.maintenance.handoff add --by "Claude Code" --summary "..." --files "..." --next "..."
```

See also: `AGENTS.md` (onboarding written for any LLM, not just Claude) and `docs/PROJECT_INSTRUCTIONS.md` (the text to paste into a Claude Project's custom instructions, for when work happens in claude.ai chat instead of Claude Code).

## Conventions

- Verify changes with `python -m py_compile <file>` before calling anything done — this is the standing expectation for this project, not optional polish.
- Don't hand-edit `PROJECT_STATE.md`, `HEALTH_REPORT.md`, `DEPENDENCY_REPORT.md`, or `ROADMAP.md` — they're build artifacts. Edit the backlog via `tools/maintenance/roadmap.py`, or the scripts under `tools/maintenance/` if a report itself needs to change.
- The maintenance auto-commit only touches the tracked report/log files (`PROJECT_STATE.md`, `HEALTH_REPORT.md`, `DEPENDENCY_REPORT.md`, `ROADMAP.md`, `HANDOFF.md`, `MAINTENANCE.md`) plus their two hand-curated data files (`tools/maintenance/data/roadmap.json`, `tools/maintenance/data/handoff.json`) — see `TRACKED_FILES` in `tools/maintenance/publish.py` for the authoritative list. Never source files. If you see it stage anything else, something's wrong.
- Real code, not descriptions of code: this project's owner prefers actual execution/verification over explanations of what code would do.
