# Aether — Roadmap

> **Auto-rendered** from `tools/maintenance/data/roadmap.json`. Don't hand-edit this file — use `python -m tools.maintenance.roadmap add/start/done`.

Generated: 2026-07-13 05:11 UTC

## In progress

_Nothing here._

## Backlog

- [ ] **#6** Check memory/semantic_memory.py for full-rebuild-on-write issue `high`
  - Same sqlite/embedding shape as hybrid_memory.py had before that fix — likely has the same bug.
- [ ] **#1** Audit agent/executor.py `medium`
  - Flagged 'not yet audited' in PERFORMANCE_NOTES.md.
- [ ] **#2** Audit agent/planner.py `medium`
  - Flagged 'not yet audited' in PERFORMANCE_NOTES.md.
- [ ] **#3** Second pass on agent/task_queue.py `medium`
  - Prior session fixed a memory leak in TaskQueue.cancel(); worth revisiting given how much of agent/ is unaudited.
- [ ] **#5** Check plugin_watcher.py poll interval and rescan scope `medium`
  - Hot-reload watcher polls the filesystem — confirm it only rescans changed files, not everything.
- [ ] **#4** Audit core/observability.py and core/task_scheduler.py `low`
  - Flagged 'not yet audited' in PERFORMANCE_NOTES.md.

## Done

_Nothing here._
