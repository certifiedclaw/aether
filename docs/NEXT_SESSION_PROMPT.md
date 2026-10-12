# Next-session kickoff prompt

Copy everything below the line into the first message of a new session
(any LLM, any tool — Claude Code, claude.ai, another model entirely) to
get it oriented on Aether with no prior context. This file itself is
hand-maintained, not auto-generated — update the "Current backlog" and
"Recently fixed" sections when they drift from `ROADMAP.md`/`HANDOFF.md`.

---

You're picking up work on **Aether**, a local-first AI assistant (PyQt6 desktop app + Ollama backend). This repo has a maintenance system specifically built so you don't need to rediscover context from scratch — **read these three files first, in this order:**

1. `PROJECT_STATE.md` — module layout, size, entry points, recent git history. Regenerate anytime with `python -m tools.maintenance.project_state`.
2. `HANDOFF.md` — narrative log of what past sessions actually found and fixed, most recent first. This is the one that matters most: it explains *why* things are the way they are, not just *what* they are.
3. `ROADMAP.md` — the current backlog (hand-curated, not auto-generated). Manage it with `python -m tools.maintenance.roadmap {list,add,start,done}`.

Also skim `HEALTH_REPORT.md` (auto-generated code-smell scan: oversized files/functions, bare excepts, stray prints, TODOs) — regenerate with `python -m tools.maintenance.health_check`.

Run all of the above at once with `python -m tools.maintenance.run_all --skip-deps` (omit `--skip-deps` if you also want a dependency-freshness check).

## Environment gotchas (don't rediscover these the hard way)

- **`main.py` can't be safely imported or run in a sandboxed/headless environment.** It auto-installs PyQt6 via pip if missing, then does `os.execv` to restart the process on import. Verify changes to `main.py` with `python -m py_compile`, `pyflakes`, and careful literal diff review — not by running it.
- `tests/stress_test.py` is a **standalone CLI benchmark script**, not a pytest suite — run it directly (`python tests/stress_test.py --iterations N [--skip-llm]`). `tests/conftest.py` tells pytest to skip it; don't remove that without renaming stress_test.py's `test_*` functions first, or `pytest tests/` will silently go back to reporting 0 tests run (this exact regression happened once already — see handoff #16).
- The maintenance auto-commit (`tools/maintenance/publish.py`) only ever touches the tracked report/log files plus their two JSON data files — never source. If you see it stage anything else, something's wrong.

## Current backlog (as of handoff #18 / roadmap state below)

Full detail and notes live in `ROADMAP.md` — this is just the priority-ordered shortlist:

| # | Priority | Item | Why it's worth doing |
|---|---|---|---|
| #10 | high-ish, narrowed | Finish the except-pass sweep — ~235 `except Exception as e: log...` blocks codebase-wide, plus `actions/`'s non-pass-only except blocks | The 62 zero-logging pass-only blocks in `actions/` are now fully reviewed (handoff #17) — no 4th live bug found there, just the #16 dead-code finding below. The remaining log-and-continue blocks are lower risk (they at least log) but still unreviewed. |
| #16 | low, needs a decision not more digging | Decide fate of the vestigial `session_memory` param across `actions/` | ~16 action functions accept `session_memory=None`, but no class implementing it exists anywhere and every call site passes `None`. `weather_report.py`'s guarded call to it never fires. Either implement the feature or strip the dead parameter — that's a product call, not something to just do unprompted. |
| #11 | medium | Refactor `ui.py`'s `__init__()` (333 lines) | Now the single largest function in the codebase (since #7 below is done) — but it's setup code, not hot-path, so lower urgency than its size suggests. |
| #12 | medium | Refactor `dashboard/server.py`'s `_build_app()` (318 lines) | Same shape as #11 — route registration, likely splits cleanly by route group. |
| #8 | low | Refactor `_ensure_network_access()` in `dashboard/server.py` (208 lines) | One-shot Windows-firewall setup path, not hot-path. |
| #13 | low | Refactor `main.py`'s `_init_new_subsystems()` (158 lines) | Startup wiring, not hot-path. |
| #14 | low | Route 174 stray `print()` calls through `core/observability.py` | Concentrated in `agent/executor.py` and `agent/task_queue.py`. Pure hygiene, no known bug behind it. |

`_execute_tool()` refactor (main.py, was 294 lines, the biggest hot-path function) is **done** — see #7 in "Recently fixed." Its extraction pattern (dispatch-dict + one-method-per-branch, watch for early-return branches that can't go through a plain dict lookup) is a reusable template for #11/#12/#8/#13 above.

## Recently fixed (so you don't re-flag them)

- **`_execute_tool()` in main.py refactored** — 294 lines down to 56, via a class-level dispatch dict + per-tool helper methods. Two branches with real early-return control flow (shutdown_aether, plugin-tool-found) were deliberately kept inline rather than force-fit into the dict, to avoid a subtle behavior change. Verified via py_compile + a zero-diff pyflakes comparison + manual cross-check of all 31 dispatch entries against the original branches (main.py still can't be run in this sandbox — see environment gotchas above).
- **`pytest tests/` was silently running 0 tests** — `stress_test.py`'s CLI-only functions aren't pytest fixtures, and a collection error aborts the whole run by default. Fixed via `tests/conftest.py`. Verified 34/34 pass now, and the standalone CLI still works unchanged.
- **`health_check.py` had a self-inflicted false-positive TODO** — its own regex matched a `#` inside a string literal (its own markdown heading). Switched to real `tokenize.COMMENT` scanning.
- **The 62 pass-only except blocks in `actions/` are reviewed** (handoff #17) — this is the exact shape that found 3 real bugs previously, so it was worth checking carefully. No 4th live bug this time; one dead-code finding logged as #16 instead of being conflated with a "live" bug.
- The full list of "audited, don't re-flag" modules is in `HANDOFF.md` entries #9–#18 — each real-bug entry found and fixed something verified (not just style nits), so read those summaries before re-auditing anything already covered there (`agent/executor.py`, `agent/planner.py`, `agent/task_queue.py`, `core/task_scheduler.py`, `core/observability.py`, `core/plugin_watcher.py`, `memory/semantic_memory.py`, `main.py`'s `_execute_tool`/`_process_message_inner`).

## Working style established by prior sessions (worth continuing)

- Reproduce a suspected bug with a real script *before* touching code, and re-verify the fix resolves that exact repro afterward — several of the real bugs found here (task_queue's dataclass-equality landmine, the two swallowed-ImportError bugs) would've been missed by code reading alone.
- Add a permanent regression test for anything fixed, in `tests/test_all_critical.py`.
- Log what you did via `python -m tools.maintenance.handoff add` before signing off — future sessions (including future-you) depend on it.
