# Claude Project Custom Instructions

This is the exact text to paste into a Claude Project's "Project
instructions" field (claude.ai → Projects → this project → settings).
It exists as a real file here so it travels with the repo instead of
living only in a chat transcript you'd have to go dig up again.

Project instructions apply automatically to every conversation started
inside that Project — this is the closest claude.ai (as opposed to Claude
Code) gets to CLAUDE.md's zero-effort auto-loading. It does NOT sync live
with this repo; if you update Aether locally, re-upload the changed files
to the Project's knowledge for future sessions to see them.

\---

```
This project is the Aether codebase (local voice AI assistant — PyQt6 UI, Ollama LLM, Whisper STT, EdgeTTS/Kokoro TTS).
Repo: https://github.com/certifiedclaw/aether-v1

Work on this project spans multiple separate sessions/instances with no shared memory between them — sometimes not even the same LLM. Before doing any work:

1. Open HANDOFF.md first. It's a running log of what previous sessions actually did and what they left for you to do next. This is the only continuity mechanism that survives across sessions — read it before anything else.
2. Open MAINTENANCE.md — auto-generated index into current project state.
3. From there: PROJECT\_STATE.md (module layout, sizes, entry points, real recent git history), HEALTH\_REPORT.md (TODOs, oversized files, bare excepts, syntax errors), and ROADMAP.md (actual backlog) as needed for the task.
4. Do NOT trust IMPROVEMENTS\_V2.md through IMPROVEMENTS\_V6.md, CHANGES.md, or CHANGELOG.md as current state — historical snapshots, not maintained.
5. If those files look stale or missing, run: python -m tools.maintenance.run\_all — then re-read them.

The GitHub repo may lag behind whatever zip or files you're handed — main.py's auto-push hook only commits the report files, not source changes, so check the actual repo (fetch https://github.com/certifiedclaw/aether) rather than assuming it's in sync if that matters for the task.

Before ending a session that did real work, add a handoff entry:
python -m tools.maintenance.handoff add --by "Claude (claude.ai chat)" --summary "..." --files "..." --next "..."
Skipping this is the most likely way work gets redone or undone across sessions — treat it as required, not optional.

Never hand-edit PROJECT\_STATE.md, HEALTH\_REPORT.md, DEPENDENCY\_REPORT.md, ROADMAP.md, or HANDOFF.md — they're regenerated/rendered automatically. Edit the backlog via python -m tools.maintenance.roadmap add/start/done.

Verify every code change with python -m py\_compile <file> before presenting it as done. Prefer real execution and verification over describing what code would do — this project's owner works that way and expects the same from Claude.

actions/osint\_tools.py has per-action OPSEC warnings built into its tool schema. Preserve them in any edit.
```

\---

## Setup checklist (do this once)

1. claude.ai → Projects → New project (or open the existing Aether one)
2. Settings → paste the block above into "Project instructions"
3. Add to knowledge: at minimum `HANDOFF.md`, `MAINTENANCE.md`,
`PROJECT\_STATE.md`, `ROADMAP.md` — or the whole zip if you'd rather not
pick files
4. Re-upload after any local change you want future sessions to know about

