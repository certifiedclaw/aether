# Aether — Agent Onboarding

This file is for any AI assistant working in this repository — Claude, GPT,
Gemini, a local model, or anything else. Some tools (Claude Code) auto-load
a tool-specific file (`CLAUDE.md`); this one has no assumed auto-loading
mechanism and is meant to be found by a general-purpose read of the repo
root, or pasted in manually if you don't have live file access at all.

## What this project is

A local, privacy-first voice AI assistant: PyQt6 desktop UI, Ollama for
local LLM inference, faster-whisper for speech-to-text, EdgeTTS/Kokoro for
text-to-speech. No cloud LLM dependency by design. Repo:
https://github.com/certifiedclaw/aether

## Read these, in order, before making changes

1. **`HANDOFF.md`** — what previous sessions (possibly a different AI
   entirely) actually did and what they left for next. Work on this project
   is expected to span multiple separate sessions/instances with **no
   shared memory between them**. This file is the only continuity
   mechanism that survives that. Read it first.
2. **`MAINTENANCE.md`** — index into the other three auto-generated
   reports below.
3. **`PROJECT_STATE.md`** — current module layout, file sizes, entry
   points, real recent git history (auto-generated, trust this over any
   hand-written doc).
4. **`HEALTH_REPORT.md`** — known code smells (oversized files/functions,
   bare excepts, stray prints, unresolved TODOs, syntax errors).
5. **`ROADMAP.md`** — the actual backlog.

Do not trust `IMPROVEMENTS_V2.md` through `IMPROVEMENTS_V6.md`,
`CHANGES.md`, or `CHANGELOG.md` as current state — they're historical
snapshots, frozen at whatever moment they were last hand-written, and nothing
keeps them in sync with the code anymore.

If the four auto-generated reports look stale, absent, or contradict what
you're seeing in the code, regenerate them:
```
python -m tools.maintenance.run_all
```

## Before you finish this session

Add a handoff entry so the next session (which may not be you, may not
even be the same model) knows what happened:
```
python -m tools.maintenance.handoff add \
  --by "<model/tool name>" \
  --summary "<what you actually did>" \
  --files "<comma-separated files/dirs touched>" \
  --next "<what should happen next>"
```
This is not optional politeness — without it, the next session has to
re-derive context from git diffs alone, which loses the *why* behind
changes. Skipping this is the single most likely way work gets redone or
undone across sessions.

## Working conventions

- Verify every change with `python -m py_compile <file>` before presenting
  it as finished. This project's owner works from real execution and
  verification, not descriptions of what code would do — that preference
  applies to you too, regardless of which model you are.
- Don't hand-edit `PROJECT_STATE.md`, `HEALTH_REPORT.md`,
  `DEPENDENCY_REPORT.md`, `ROADMAP.md`, or `HANDOFF.md` — they're rendered
  from `tools/maintenance/data/*.json`. Edit the backlog via
  `tools/maintenance/roadmap.py`, not by hand.
- `actions/osint_tools.py` has per-action OPSEC warnings built into its
  tool schema. Preserve them in any edit — don't strip them for brevity.
- `main.py`'s `main()` runs an auto-commit-and-push of the four report
  files (not source code) on every launch, using whatever git credentials
  are configured on the machine it runs on. If you're editing that hook,
  know that `write_if_changed()` in `tools/maintenance/__init__.py` exists
  specifically to prevent it from committing on every single launch
  regardless of whether anything substantive changed — don't remove that
  without understanding why (see `HANDOFF.md` #1 for the bug it fixes).

## If you don't have file access at all

If someone has pasted this file to you directly rather than giving you
repo access, ask them to also paste `HANDOFF.md` and `PROJECT_STATE.md` —
those two are the highest-value context for the lowest amount of text.
