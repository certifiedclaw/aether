# Aether — Project State

> **Auto-generated.** Do not hand-edit — rerun `python -m tools.maintenance.project_state` (or `run_all`) to refresh. This is meant to be the one file a new session (human or Claude) reads first to get oriented.

Generated: 2026-10-14 01:06 UTC
Version: Aether v1.0.0

## Entry points
- `main.py` (present)
- `ui.py` (present)
- `run.sh` (present)
- `run.bat` (present)
- `self_test.py` (present)
- `evaluator.py` (present)

## Module layout

| Directory | .py files | Total lines |
|---|---|---|
| `./` | 12 | 5,644 |
| `actions/` | 23 | 11,721 |
| `agent/` | 4 | 1,163 |
| `config/` | 1 | 27 |
| `core/` | 48 | 11,169 |
| `dashboard/` | 3 | 1,255 |
| `dev_tools/` | 1 | 78 |
| `integrations/` | 4 | 1,077 |
| `memory/` | 6 | 1,173 |
| `plugins/` | 1 | 44 |
| `security/` | 7 | 1,338 |
| `tests/` | 36 | 7,988 |
| `tools/` | 18 | 2,552 |
| **Total** | **164** | **45,229** |

## Modules with docstrings (top-level summary)

### `actions/`
- `actions/browser_control.py` (913 lines, 2 classes, 10 functions)
- `actions/code_helper.py` (579 lines, 0 classes, 23 functions)
- `actions/computer_control.py` (510 lines, 0 classes, 22 functions)
- `actions/computer_settings.py` (734 lines, 0 classes, 63 functions)
- `actions/desktop.py` (657 lines, 0 classes, 22 functions)
- `actions/desktop_runner.py` (43 lines, 0 classes, 1 functions) — actions/desktop_runner.py -- child process that runs approved AI-generated desktop code.
- `actions/dev_agent.py` (554 lines, 1 classes, 16 functions)
- `actions/extra_tools.py` (1077 lines, 0 classes, 46 functions) — extra_tools.py — Aether v3 Extended Capabilities
- `actions/file_controller.py` (647 lines, 0 classes, 26 functions)
- `actions/file_processor.py` (951 lines, 0 classes, 19 functions) — file_processor.py — Aether Universal File Processor
- `actions/flight_finder.py` (348 lines, 0 classes, 9 functions)
- `actions/game_updater.py` (1050 lines, 0 classes, 39 functions)
- `actions/open_app.py` (358 lines, 0 classes, 8 functions)
- `actions/osint_tools.py` (1356 lines, 2 classes, 23 functions) — osint_tools.py — Aether v3 OSINT Toolkit
- `actions/pdf_tools.py` (113 lines, 0 classes, 6 functions) — pdf_tools.py — Aether v3 PDF Tools (PyMuPDF)
- `actions/proactive.py` (117 lines, 1 classes, 0 functions) — ProactiveEngine 2.0 - context-aware, time-aware, non-repetitive background prompts.
- `actions/reminder.py` (332 lines, 0 classes, 9 functions)
- `actions/screen_processor.py` (275 lines, 0 classes, 13 functions) — Aether — Screen / Camera Processor
- `actions/send_message.py` (268 lines, 0 classes, 17 functions)
- `actions/system_monitor.py` (213 lines, 1 classes, 4 functions) — System Monitor — background metric checks with voice alert support.
- `actions/weather_report.py` (83 lines, 0 classes, 3 functions)
- `actions/web_search.py` (128 lines, 0 classes, 6 functions)
- `actions/youtube_video.py` (415 lines, 0 classes, 16 functions)

### `agent/`
- `agent/error_handler.py` (150 lines, 1 classes, 3 functions) — Aether — Error Handler
- `agent/executor.py` (457 lines, 2 classes, 6 functions) — Aether — Agent Executor
- `agent/planner.py` (305 lines, 0 classes, 5 functions) — Aether — Task Planner
- `agent/task_queue.py` (251 lines, 4 classes, 1 functions)

### `config/`
- `config/__init__.py` (27 lines, 0 classes, 5 functions)

### `core/`
- `core/__init__.py` (2 lines, 0 classes, 0 functions)
- `core/archive_safety.py` (21 lines, 0 classes, 1 functions)
- `core/chat_commands.py` (242 lines, 1 classes, 0 functions) — core/chat_commands.py — Aether Built-in Chat Commands
- `core/chat_history.py` (286 lines, 1 classes, 9 functions) — Bounded, atomic local storage for named chat transcripts.
- `core/config_io.py` (88 lines, 0 classes, 4 functions) — core/config_io.py -- save the settings dialog's values WITHOUT destroying the rest.
- `core/config_validator.py` (201 lines, 2 classes, 9 functions) — core/config_validator.py — Aether Configuration Validator
- `core/conversation_core.py` (975 lines, 1 classes, 5 functions) — core/conversation_core.py — Aether Conversation Core
- `core/crash_logger.py` (141 lines, 0 classes, 4 functions) — core/crash_logger.py — Aether Crash Recovery Logging
- `core/credentials.py` (149 lines, 0 classes, 11 functions) — core/credentials.py -- one place to ask "what is the API key for X?".
- `core/device_specs.py` (149 lines, 2 classes, 4 functions) — device_specs.py — best-effort local hardware detection + Ollama model
- `core/diagnostics.py` (135 lines, 1 classes, 2 functions) — diagnostics.py — Aether System Diagnostics
- `core/echo.py` (388 lines, 2 classes, 5 functions) — Telling the user's voice apart from our own coming back through the speakers.
- `core/evaluator.py` (130 lines, 2 classes, 0 functions) — core/evaluator.py — Aether Self-Evaluation System
- `core/file_watcher.py` (126 lines, 1 classes, 1 functions) — core/file_watcher.py — Aether File Watcher
- `core/goal_state.py` (308 lines, 5 classes, 0 functions) — core/goal_state.py — Aether Goal & State Manager
- `core/headless_ui.py` (165 lines, 2 classes, 1 functions) — headless_ui.py — Headless drop-in replacement for ui.py's AetherUI.
- `core/hf_offline.py` (36 lines, 0 classes, 1 functions) — core/hf_offline.py -- temporary network access for first-run model downloads.
- `core/hotkey_manager.py` (61 lines, 1 classes, 0 functions) — hotkey_commands.py — Aether Global Hotkey Manager (stub)
- `core/installer.py` (164 lines, 0 classes, 3 functions) — Aether — Dependency auto-installer.
- `core/known_folders.py` (66 lines, 0 classes, 2 functions) — core/known_folders.py -- real user folder locations on Windows.
- `core/listen_loops.py` (274 lines, 2 classes, 0 functions) — core/listen_loops.py — AetherLocal's microphone/STT background loops.
- `core/live_report.py` (211 lines, 0 classes, 6 functions) — core/live_report.py — Live per-turn usage dump.
- `core/llm_client.py` (1332 lines, 0 classes, 33 functions) — Local LLM client for Aether.
- `core/loop_guard.py` (85 lines, 2 classes, 0 functions) — core/loop_guard.py — Aether Loop Guard
- `core/mcp_bridge.py` (317 lines, 2 classes, 1 functions) — mcp_bridge.py — Aether v4.1 MCP (Model Context Protocol) Bridge
- `core/memory_hygiene.py` (48 lines, 0 classes, 4 functions) — core/memory_hygiene.py -- keep failed turns out of long-term memory.
- `core/memory_tools.py` (379 lines, 0 classes, 5 functions) — memory/memory_tools.py — Aether Memory Backup, Export, Import & Pruning
- `core/notify.py` (54 lines, 0 classes, 1 functions) — notify.py — Push notifications (ntfy.sh, or a self-hosted ntfy instance)
- `core/observability.py` (324 lines, 5 classes, 1 functions) — core/observability.py — Aether Observability Layer
- `core/pending_approvals.py` (129 lines, 2 classes, 1 functions) — pending_approvals.py — In-memory store for headless remote-approval requests.
- `core/plugin_watcher.py` (108 lines, 0 classes, 1 functions) — plugin_watcher.py — Aether v4.1 Plugin Hot-Reload
- `core/plugins.py` (218 lines, 1 classes, 1 functions) — plugins.py - Aether plugin system.
- `core/prompt_builder.py` (110 lines, 1 classes, 0 functions) — core/prompt_builder.py — Aether System Prompt Assembly
- `core/protected_paths.py` (42 lines, 0 classes, 1 functions) — core/protected_paths.py -- files the model must never be able to read through any tool.
- `core/quick_intents.py` (169 lines, 0 classes, 4 functions) — core/quick_intents.py -- run simple voice commands without asking the LLM.
- `core/risk.py` (425 lines, 0 classes, 17 functions) — core/risk.py -- one declarative table of what each tool/action is allowed to do.
- `core/safety.py` (118 lines, 2 classes, 2 functions) — safety.py — Aether v4.1 Safety & Verification Layer
- `core/self_test.py` (471 lines, 2 classes, 22 functions) — core/self_test.py — Aether Self-Test Mode
- `core/smart_context.py` (152 lines, 0 classes, 3 functions) — smart_context.py — Aether v3 Context Compression (stub / implementation)
- `core/stt.py` (80 lines, 2 classes, 0 functions) — Speech-to-Text engines for Aether.
- `core/task_scheduler.py` (105 lines, 2 classes, 0 functions) — task_scheduler.py — Aether v3 Task Scheduler (stub / minimal implementation)
- `core/tier_router.py` (181 lines, 2 classes, 6 functions) — Tier-based LLM routing for Aether.
- `core/tool_declarations.py` (585 lines, 0 classes, 3 functions) — core/tool_declarations.py — Aether's built-in tool schema.
- `core/tool_dispatch.py` (481 lines, 1 classes, 0 functions) — core/tool_dispatch.py — Aether Tool Dispatch
- `core/tool_router.py` (128 lines, 0 classes, 4 functions) — tool_router.py — per-turn tool subset selection for EVERYDAY-tier turns.
- `core/tts.py` (442 lines, 4 classes, 6 functions) — Text-to-Speech engines for Aether.
- `core/undo.py` (103 lines, 2 classes, 4 functions) — core/undo.py — Aether Undo Stack
- `core/wake_word.py` (265 lines, 2 classes, 3 functions) — Local wake-word detection for JARVIS ("Hey Jarvis").

### `dashboard/`
- `dashboard/__init__.py` (1 lines, 0 classes, 0 functions)
- `dashboard/api_secure.py` (126 lines, 2 classes, 4 functions) — dashboard/api_secure.py — Stage 2 of the remote-access build (see HANDOFF.md #3).
- `dashboard/server.py` (1128 lines, 2 classes, 14 functions) — dashboard/server.py — AETHER Local Dashboard

### `dev_tools/`
- `dev_tools/dump.py` (78 lines, 0 classes, 1 functions) — dump.py — MARK XL Codebase Exporter

### `integrations/`
- `integrations/__init__.py` (14 lines, 0 classes, 0 functions) — integrations/ — Aether v3 external framework integrations.
- `integrations/langchain_tools.py` (457 lines, 0 classes, 14 functions) — integrations/langchain_tools.py — Aether × LangChain
- `integrations/llama_index_rag.py` (339 lines, 0 classes, 12 functions) — integrations/llama_index_rag.py — Aether × LlamaIndex
- `integrations/oi_shell.py` (267 lines, 0 classes, 10 functions) — integrations/oi_shell.py — Aether × Open Interpreter

### `memory/`
- `memory/__init__.py` (1 lines, 0 classes, 0 functions)
- `memory/config_manager.py` (166 lines, 1 classes, 8 functions)
- `memory/hybrid_memory.py` (403 lines, 3 classes, 0 functions) — memory/hybrid_memory.py — Aether Hybrid Memory
- `memory/memory_manager.py` (215 lines, 0 classes, 12 functions)
- `memory/rag.py` (179 lines, 2 classes, 0 functions) — memory/rag.py — Aether Hybrid RAG Engine
- `memory/semantic_memory.py` (209 lines, 2 classes, 0 functions) — memory_engine.py — Aether v3 Memory Engine

### `plugins/`
- `plugins/_template.py` (44 lines, 0 classes, 1 functions) — Drop-in Aether plugin template.

### `security/`
- `security/__init__.py` (44 lines, 0 classes, 0 functions) — security — Aether remote-access security layer (v4.1+).
- `security/audit.py` (156 lines, 0 classes, 5 functions) — security/audit.py — append-only security event log.
- `security/capabilities.py` (121 lines, 2 classes, 2 functions) — security/capabilities.py — gated actions + risk levels.
- `security/first_run.py` (143 lines, 0 classes, 4 functions) — security/first_run.py — first-time setup, run from Aether's desktop UI.
- `security/middleware.py` (211 lines, 0 classes, 7 functions) — security/middleware.py — FastAPI dependencies for capability / session checks.
- `security/policy.py` (191 lines, 2 classes, 2 functions) — security/policy.py — runtime approval flow for HIGH-risk capabilities.
- `security/tokens.py` (472 lines, 3 classes, 1 functions) — security/tokens.py — SecurityManager: state + token issuance + device pairing.

### `tests/`
- `tests/conftest.py` (64 lines, 0 classes, 3 functions) — pytest configuration for tests/.
- `tests/stress_test.py` (369 lines, 0 classes, 15 functions) — stress_test.py — Aether Comprehensive Stress Test
- `tests/test_all_critical.py` (2151 lines, 0 classes, 90 functions)
- `tests/test_apply_handoff.py` (66 lines, 0 classes, 6 functions) — tools/apply_handoff_v12.py records the session once, through the handoff module.
- `tests/test_apply_roadmap.py` (88 lines, 0 classes, 7 functions) — tools/apply_roadmap_v12.py must add items through the roadmap module, idempotently.
- `tests/test_chat_commands.py` (117 lines, 0 classes, 5 functions)
- `tests/test_chat_history.py` (118 lines, 0 classes, 8 functions)
- `tests/test_config_io.py` (159 lines, 1 classes, 17 functions) — Saving the settings dialog must not delete settings it does not show, and must keep
- `tests/test_core.py` (90 lines, 1 classes, 7 functions)
- `tests/test_credentials.py` (335 lines, 1 classes, 32 functions) — core/credentials.py + tools/secrets_cli.py: env > OS keyring > config file, never logged,
- `tests/test_dangerous_confirmation.py` (74 lines, 0 classes, 7 functions) — Shutdown/restart must be approved by the USER (UI dialog), never by a model-supplied
- `tests/test_dashboard_security.py` (204 lines, 0 classes, 17 functions) — Dashboard: encrypt-then-MAC, random session keys, login lockout (roadmap #33/#34).
- `tests/test_desktop_sandbox.py` (229 lines, 0 classes, 16 functions) — Roadmap #23: the desktop task sandbox must not be escapable, and model-
- `tests/test_desktop_timeout.py` (121 lines, 0 classes, 14 functions) — Approved desktop code runs in a child process with a deadline: `while True: pass`
- `tests/test_execution_gates.py` (293 lines, 0 classes, 22 functions) — Tools that execute model-written or model-chosen code must be approved by the USER
- `tests/test_invariants.py` (675 lines, 1 classes, 51 functions) — Whole-codebase invariants: properties that must hold across ALL tools / ALL inputs.
- `tests/test_live_grader.py` (117 lines, 0 classes, 17 functions) — The grader must FAIL on the real bad replies seen in logs and PASS on the good ones.
- `tests/test_maintenance_publish.py` (39 lines, 0 classes, 2 functions)
- `tests/test_mark_lv_ports.py` (288 lines, 2 classes, 23 functions) — Tests for the MARK LV ports: EchoGuard/OutputTap, WakeGate, Gemini opt-in route.
- `tests/test_mcp_bridge.py` (234 lines, 0 classes, 11 functions)
- `tests/test_openai_api.py` (99 lines, 0 classes, 3 functions)
- `tests/test_osint_tools.py` (209 lines, 2 classes, 8 functions)
- `tests/test_pending_approvals.py` (23 lines, 0 classes, 2 functions)
- `tests/test_plugin_trust.py` (37 lines, 0 classes, 1 functions)
- `tests/test_protected_paths.py` (233 lines, 0 classes, 16 functions) — Secrets files must not be readable through any tool; port scans are bounded.
- `tests/test_quick_intents.py` (195 lines, 0 classes, 15 functions) — Quick-intent shortcuts: the 15 live prompts plus false-positive guards.
- `tests/test_req_check.py` (79 lines, 0 classes, 6 functions) — verify_local must flag packages that requirements.txt declares but are missing.
- `tests/test_risk.py` (364 lines, 0 classes, 33 functions) — core/risk.py: one declarative tier table, enforced at the entry function of every tool
- `tests/test_risk_task.py` (260 lines, 2 classes, 24 functions) — The TASK tier: ask once per user request (quoting the user), cover that tool for that
- `tests/test_routing_and_paths.py` (85 lines, 0 classes, 10 functions) — Regression tests for tier routing, known folders, weather and open_app honesty.
- `tests/test_security_fixes.py` (61 lines, 0 classes, 5 functions) — calculate() must not allow attribute/dunder escapes or runaway powers; open_app must not shell out.
- `tests/test_stream_cancellation.py` (154 lines, 0 classes, 4 functions)
- `tests/test_tool_call_recovery.py` (83 lines, 0 classes, 10 functions) — Recovery of raw <tool_call> text, held-back streaming, plain monitor alerts, HF offline restore.
- `tests/test_v12e_fixes.py` (113 lines, 0 classes, 8 functions) — Regression tests for the v12d review fixes (each fails against v12d).
- `tests/test_v12f_fixes.py` (40 lines, 0 classes, 5 functions) — v12f: LangChain must not pretend to have researched; live log isolation.
- `tests/test_v12g_fixes.py` (122 lines, 1 classes, 11 functions) — v12g: a missing Ollama model / missing config must fail loudly and early, not as 24 silent 404s.

### `tools/`
- `tools/__init__.py` (1 lines, 0 classes, 0 functions)
- `tools/apply_handoff_v12.py` (171 lines, 0 classes, 2 functions) — Record the v12 session in the handoff log through the project's own handoff code.
- `tools/apply_roadmap_v12.py` (113 lines, 0 classes, 3 functions) — Add the v12 backlog items through the project's own roadmap code (never hand-edit).
- `tools/clean_memory.py` (86 lines, 0 classes, 2 functions) — Find and remove junk rows from Aether's exchange memory (data/memory.db).
- `tools/fix_local_state.py` (100 lines, 0 classes, 3 functions) — Clean up local state damaged by the old prompt.txt few-shot examples.
- `tools/live_grader.py` (225 lines, 0 classes, 11 functions) — tools/live_grader.py -- grade a headless replay against tests/live_test_cases.json.
- `tools/maintenance/__init__.py` (58 lines, 0 classes, 2 functions) — Aether Maintenance Toolkit
- `tools/maintenance/dep_watcher.py` (183 lines, 0 classes, 7 functions) — Checks Aether's dependencies against their upstream sources and writes
- `tools/maintenance/git_setup.py` (78 lines, 0 classes, 2 functions) — One-time helper for getting this checkout properly connected to
- `tools/maintenance/handoff.py` (138 lines, 0 classes, 7 functions) — Session handoff log.
- `tools/maintenance/health_check.py` (201 lines, 0 classes, 6 functions) — Scans the codebase for common maintenance smells and writes
- `tools/maintenance/project_state.py` (172 lines, 0 classes, 6 functions) — Generates PROJECT_STATE.md — a single, always-current snapshot of the
- `tools/maintenance/publish.py` (106 lines, 0 classes, 5 functions) — Commits the generated maintenance reports (and roadmap.json) to git and
- `tools/maintenance/roadmap.py` (203 lines, 0 classes, 11 functions) — Roadmap / backlog tracker.
- `tools/maintenance/run_all.py` (103 lines, 0 classes, 1 functions) — Runs every maintenance check and regenerates all reports in one shot:
- `tools/req_check.py` (102 lines, 0 classes, 5 functions) — tools/req_check.py -- which packages declared in requirements.txt are not installed?
- `tools/secrets_cli.py` (157 lines, 0 classes, 7 functions) — tools/secrets_cli.py -- keep API keys in the OS credential store instead of a JSON file.
- `tools/verify_local.py` (355 lines, 1 classes, 11 functions) — tools/verify_local.py -- one-shot health check for a real Aether install.

## Recent history (git log)
- 53a90bf 2026-10-13 Merge pull request #5 from certifiedclaw/fix-dashboard-test-env
- 6f974d6 2026-10-13 Merge pull request #4 from certifiedclaw/codex/security-audit-fixes
- a4cc8ab 2026-10-13 fix: reject late remote approvals
- 6a50510 2026-10-13 fix: bound MCP server RPC waits
- 3e0be2b 2026-10-13 fix: keep dashboard credentials out of URLs
- 87873cf 2026-10-13 fix: cap archive extraction expansion
- 27a6b23 2026-10-13 fix: isolate and gate MCP tool calls
- 5756732 2026-10-13 fix: harden agent writes and dashboard uploads

## See also
- `HEALTH_REPORT.md` — automated code health scan
- `DEPENDENCY_REPORT.md` — outdated / risky dependency scan
- `ROADMAP.md` — hand-curated backlog (edit via `tools/maintenance/roadmap.py`)
