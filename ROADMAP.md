# Aether — Roadmap

> **Auto-rendered** from `tools/maintenance/data/roadmap.json`. Don't hand-edit this file — use `python -m tools.maintenance.roadmap add/start/done`.

Generated: 2026-10-13 01:42 UTC

## In progress

- [ ] **#10** Finish the except-pass sweep across actions/ (~90 blocks unreviewed) `low`
  - Handoff #15's sweep covered core/, security/, agent/, dashboard/server.py, main.py, core/llm_client.py (~20 of 111 bare except-pass blocks). This session covered the 62 pass-only (zero-logging, highest-risk shape) blocks specifically in actions/ -- reviewed all of them; found one dead-code path (see #16) but no active silently-broken call like the 3 prior real bugs. Still unreviewed: the ~235 'except Exception as e: log...' blocks across the whole codebase (lower risk shape since they at least log, but not zero -- worth a pass if time allows) and actions/'s non-pass-only except blocks.

## Backlog

- [ ] **#25** Secrets handling: stop shipping config/api_keys.json in zips; add .gitignore, example template, env/credential-store option `high`
  - Live Gemini key is inside full-folder zips. Verify git history never contained it; rotate if it did.
- [ ] **#36** Plugins: write down the trust model (plugins/ *.py are hot-loaded and run fully trusted) and consider an explicit enable/hash allowlist `high`
  - Raised by an external audit of the v12 tree. core/plugins.py exec_module's every plugins/*.py; file_controller now refuses the install folder, so the model cannot drop one in, but nothing else vets them.
- [ ] **#26** Retry once (about 1s backoff) on Gemini 503/429 before falling back to local `medium`
  - Seen in two consecutive verify runs; local HARD fallback costs 2-4s more.
- [ ] **#37** run_python is not a sandbox: decide keep (ASK + full-code dialog + scrubbed env, as of v12g) or replace with a real isolation layer `medium`
  - v12g: same interpreter, -I, scratch cwd, secret env stripped, timeout clamped 1-60s, dialog shows whole code. Still runs as the user.
- [ ] **#14** Route the 174 stray print() calls through core/observability.py `low`
  - Flagged in HEALTH_REPORT.md's stray-print check. Concentrated in agent/executor.py, agent/task_queue.py, and other core/ modules — actions/ is intentionally exempt (CLI-facing tool output).
- [ ] **#16** Decide fate of the vestigial session_memory param across actions/ `low`
  - ~16 action functions accept session_memory=None in their signature (weather_report.py, open_app.py, reminder.py, code_helper.py, dev_agent.py, browser_control.py, send_message.py, computer_settings.py, file_controller.py, screen_processor.py, web_search.py, desktop.py, youtube_video.py, computer_control.py...) but no SessionMemory-like class exists anywhere and every call site passes None or omits it -- confirmed via grep. weather_report.py's 'session_memory.set_last_search(...)' (guarded by 'if session_memory:') is dead code as a result, not an active bug like the plugin_watcher/config_cache ones -- it never fires. Either implement the intended feature or strip the dead parameter across all ~16 signatures.
- [ ] **#19** Pending approvals don't survive a restart `low`
  - core/pending_approvals.ApprovalStore is in-memory only. A dashboard/process restart while something's mid-approval loses it -- the calling thread just times out and denies (safe default, not a security hole), but the operator gets no indication the original request ever existed. Worth deciding whether that's acceptable long-term or if approvals should persist (e.g. to disk/sqlite) across restarts.
- [ ] **#20** ntfy push for approvals is one-shot, no retry/queue `low`
  - core/notify.send_ntfy() is fire-and-forget -- if the push fails (network blip, ntfy down), the approval still shows up in the dashboard's Pending Approvals list, but the operator isn't proactively re-notified and may not know to check. Consider a retry with backoff, or re-pushing once if unresolved after N seconds.
- [ ] **#21** dashboard/server.py has no standalone entrypoint `low`
  - Noted since the original headless-mode work: start_dashboard_server() only ever gets called from AetherLocal._init_dashboard_and_integrations(), itself only reachable after the full AetherLocal boot sequence runs. Fine for the current headless design (still boots AetherLocal, just skips the GUI), but if a lighter API-only deployment (dashboard + approvals + LLM status, nothing else) is ever wanted, this coupling needs breaking.
- [ ] **#24** Delete stray root copies: loop_guard.py, memory_tools.py, self_test.py (stale vs core/), dump.py (vs dev_tools/) `low`
  - Nothing imports them; self_test.py and dump.py have drifted from their real copies. Confirm no external script uses them, then delete.
- [ ] **#29** Review shell=True call sites (dev_agent._open_vscode, computer_settings, dashboard/server) `low`
  - dev_agent passes a project-derived path with shell=True on Windows: metacharacters in a project name could inject. computer_settings strings are constant.
- [ ] **#30** Make test_create_file_new_file_undo_removes_it pass on Linux `low`
  - Only failing test in a Linux sandbox run; passes on Windows.
- [ ] **#31** Fix dep_watcher lookups for Ollama, faster-whisper and Kokoro `low`
  - DEPENDENCY_REPORT.md shows lookup failed for all three upstream checks.
- [ ] **#32** Investigate long_term.json identity entries missing value `low`
  - Handoff guesses user redaction; unconfirmed.

## Done

- [x] **#1** Audit agent/executor.py
  - Flagged 'not yet audited' in PERFORMANCE_NOTES.md.
- [x] **#2** Audit agent/planner.py
  - Flagged 'not yet audited' in PERFORMANCE_NOTES.md.
- [x] **#3** Second pass on agent/task_queue.py
  - Prior session fixed a memory leak in TaskQueue.cancel(); worth revisiting given how much of agent/ is unaudited.
- [x] **#4** Audit core/observability.py and core/task_scheduler.py
  - Flagged 'not yet audited' in PERFORMANCE_NOTES.md.
- [x] **#5** Check plugin_watcher.py poll interval and rescan scope
  - Hot-reload watcher polls the filesystem — confirm it only rescans changed files, not everything.
- [x] **#6** Check memory/semantic_memory.py for full-rebuild-on-write issue
  - Same sqlite/embedding shape as hybrid_memory.py had before that fix — likely has the same bug.
- [x] **#7** Refactor _execute_tool() in main.py (294 lines)
  - main.py's biggest remaining function after handoff #8 broke up _process_message_inner(). Same treatment: extract pure, behavior-preserving helper methods, verify with py_compile + pyflakes + literal diff review (main.py can't safely be imported/run in this sandbox -- see handoff #8).
- [x] **#8** Refactor _ensure_network_access() in dashboard/server.py (208 lines)
  - Second-biggest oversized function per HEALTH_REPORT.md. Lower priority than main.py's since it's a one-shot Windows-firewall setup path, not hot-path chat/agent logic.
- [x] **#9** Grep for silent except-swallow blocks that might hide another load-bearing bug
  - The plugin_watcher.py hot-reload bug (handoff #13) was invisible for as long as this code existed because a bare AttributeError from a missing reload() method was caught and just logged. Worth a dedicated pass over 'except Exception: pass' / 'except Exception as e: log...' blocks across the codebase specifically looking for ones swallowing something load-bearing rather than genuinely optional, the way this whole audit series (handoffs #6, #9-#14) kept finding real bugs by actually reading and exercising code rather than skimming for style issues.
- [x] **#11** Refactor ui.py __init__() (333 lines)
  - Largest single oversized function per HEALTH_REPORT.md — bigger than main.py's _execute_tool() (#7). Same extraction treatment: pull out pure, behavior-preserving setup helpers, verify with py_compile + literal diff review.
- [x] **#12** Refactor dashboard/server.py _build_app() (318 lines)
  - Second-largest oversized function per HEALTH_REPORT.md, close behind ui.py's __init__. Route-registration/setup function — likely splits cleanly by route group.
- [x] **#13** Refactor main.py _init_new_subsystems() (158 lines)
  - One-shot startup wiring, not hot-path — lower priority than _execute_tool() (#7) and _build_app() above, but same shape of oversized-function debt.
- [x] **#15** Fix stress_test.py pytest collection failure + bare excepts + health_check.py TODO false-positive
  - pytest tests/ was aborting entirely (8 collection errors from stress_test.py's non-fixture test_* signatures) instead of running the real 34-test suite. Fixed via tests/conftest.py collect_ignore, both bare excepts changed to except Exception, and health_check.py's TODO_RE switched from a raw-line regex to real tokenize.COMMENT tokens (was matching a '#' inside this file's own markdown-heading string literal).
- [x] **#17** Headless server mode (AETHER_HEADLESS=1) + dual-host LLM fallback
  - core/headless_ui.py drop-in for ui.AetherUI with no PyQt6 dependency; main.py gates the PyQt6 bootstrap/import and defensively wraps the sounddevice import on AETHER_HEADLESS=1. core/llm_client.get_llm_settings() gained dual-host resolution -- prefers llm_remote_url (e.g. a home PC's GPU-backed Ollama) when reachable (cached health-check, default 20s interval / 2.5s timeout), falls back to llm_local_url (CPU on this machine) otherwise; unchanged behavior with no llm_remote_url configured. Verified via 9 tests + full suite.
- [x] **#18** Remote-approval flow for headless confirm_blocking() + dashboard LLM backend status
  - core/pending_approvals.py (thread-safe ApprovalStore, event-based blocking, fails closed on timeout) + core/notify.py (ntfy push -- new integration, no ntfy setup existed in this codebase before; ntfy_base_url/ntfy_topic added as new config keys). confirm_blocking() registers a pending approval, fires the push, and blocks until dashboard/server.py's new POST /api/approvals/{id}/approve|deny resolves it or times out. AETHER_HEADLESS_AUTO_CONFIRM=1 bypass unchanged. Dashboard also gained GET /api/llm-status surfacing core.llm_client.get_llm_backend_status() (active backend + real IP/host, including the fallback IP), polled in app.html. Verified via 9 tests + full suite; caught and fixed an old test that started blocking for the real 120s default timeout once confirm_blocking() became a genuine blocking call.
- [x] **#22** Client-side parser for raw <tool_call> text from Ollama (needs user approval)
  - 4 of 7 failed prompts showed raw <tool_call>{...}</tool_call> instead of acting. Touches streaming path and makes model text executable, so get explicit go-ahead; check ollama --version first. See handoff #37.
- [x] **#23** actions/desktop.py exec() sandbox is escapable and runs model-written code without a confirmation
  - safe_builtins exposes getattr and the sandbox passes Path/time objects, so getattr(Path.__init__,'__globals__') reaches os/sys; Windows also exposes ctypes. Needs a design decision: confirmation via core/safety, or a real allowlist DSL instead of exec. See handoff #39.
- [x] **#27** Execution timeout for AI-generated desktop code
  - exec() cannot be killed in-process; approved code with an infinite loop hangs its thread. Run in a worker with a deadline.
- [x] **#28** verify_local.py: flag packages declared in requirements.txt but not installed
  - watchdog and keyboard were missing for several runs, disabling plugin hot-reload and the ctrl+alt+j hotkey without any verify warning.
- [x] **#33** Dashboard: replace AES-CBC (no MAC, key = SHA-256 of the 6-char session code) with AES-GCM and a random per-session key
  - dashboard/server.py _aes_key/decrypt: anyone who captures one ciphertext can brute-force the ~30-bit code offline in seconds, and CBC without a MAC is malleable. Needs a matching change in the CryptoJS client in dashboard/static, so it must be tested with a real phone/browser. Over plain HTTP (no config/certs) the traffic is also sniffable.
- [x] **#34** Dashboard: rate-limit the one-time connect key and bind to the LAN interface only
  - No attempt limiting on /connect key checks; 6-char key, 10 min window. Server binds 0.0.0.0 (intentional for phone remote) - consider per-IP lockout after N bad keys.
- [x] **#35** Audit remaining tools that take arbitrary paths against core/protected_paths (screen_process, game_updater, reminder, send_message attachments)
  - v12f routed extra_tool diff/word_count, file_processor and pdf_tool through is_protected(); the others were not read.
- [x] **#38** Dashboard still binds 0.0.0.0 over plain HTTP by default; add LAN-only/bind option and prefer HTTPS
