# Aether — Project State

> **Auto-generated.** Do not hand-edit — rerun `python -m tools.maintenance.project_state` (or `run_all`) to refresh. This is meant to be the one file a new session (human or Claude) reads first to get oriented.

Generated: 2026-10-30 22:19 UTC
Version: Aether v1.0.0

## Entry points
- `main.py` (present)
- `ui.py` (present)
- `run.sh` (present)
- `run.bat` (present)
- `core/self_test.py` (present)

## Module layout

| Directory | .py files | Total lines |
|---|---|---|
| `./` | 3 | 4,894 |
| `actions/` | 23 | 11,860 |
| `agent/` | 4 | 1,163 |
| `config/` | 1 | 25 |
| `core/` | 61 | 16,318 |
| `dashboard/` | 5 | 1,637 |
| `dev_tools/` | 1 | 78 |
| `integrations/` | 4 | 1,144 |
| `memory/` | 6 | 1,242 |
| `plugins/` | 1 | 44 |
| `security/` | 7 | 1,340 |
| `tests/` | 68 | 13,123 |
| `tools/` | 20 | 3,370 |
| **Total** | **204** | **56,238** |

## Modules with docstrings (top-level summary)

### `actions/`
- `actions/browser_control.py` (941 lines, 2 classes, 12 functions)
- `actions/code_helper.py` (579 lines, 0 classes, 23 functions)
- `actions/computer_control.py` (508 lines, 0 classes, 22 functions)
- `actions/computer_settings.py` (755 lines, 0 classes, 65 functions)
- `actions/desktop.py` (656 lines, 0 classes, 22 functions)
- `actions/desktop_runner.py` (43 lines, 0 classes, 1 functions) — actions/desktop_runner.py -- child process that runs approved AI-generated desktop code.
- `actions/dev_agent.py` (581 lines, 1 classes, 17 functions)
- `actions/extra_tools.py` (1078 lines, 0 classes, 46 functions) — extra_tools.py — Aether v3 Extended Capabilities
- `actions/file_controller.py` (651 lines, 0 classes, 26 functions)
- `actions/file_processor.py` (951 lines, 0 classes, 19 functions) — file_processor.py — Aether Universal File Processor
- `actions/flight_finder.py` (349 lines, 0 classes, 9 functions)
- `actions/game_updater.py` (1052 lines, 0 classes, 39 functions)
- `actions/open_app.py` (357 lines, 0 classes, 8 functions)
- `actions/osint_tools.py` (1356 lines, 2 classes, 23 functions) — osint_tools.py — Aether v3 OSINT Toolkit
- `actions/pdf_tools.py` (132 lines, 0 classes, 6 functions) — pdf_tools.py — Aether v3 PDF Tools (PyMuPDF)
- `actions/proactive.py` (117 lines, 1 classes, 0 functions) — ProactiveEngine 2.0 - context-aware, time-aware, non-repetitive background prompts.
- `actions/reminder.py` (330 lines, 0 classes, 9 functions)
- `actions/screen_processor.py` (271 lines, 0 classes, 13 functions) — Aether — Screen / Camera Processor
- `actions/send_message.py` (266 lines, 0 classes, 17 functions)
- `actions/system_monitor.py` (213 lines, 1 classes, 4 functions) — System Monitor — background metric checks with voice alert support.
- `actions/weather_report.py` (76 lines, 0 classes, 3 functions)
- `actions/web_search.py` (183 lines, 0 classes, 9 functions)
- `actions/youtube_video.py` (415 lines, 0 classes, 16 functions)

### `agent/`
- `agent/error_handler.py` (150 lines, 1 classes, 3 functions) — Aether — Error Handler
- `agent/executor.py` (457 lines, 2 classes, 6 functions) — Aether — Agent Executor
- `agent/planner.py` (305 lines, 0 classes, 5 functions) — Aether — Task Planner
- `agent/task_queue.py` (251 lines, 4 classes, 1 functions)

### `config/`
- `config/__init__.py` (25 lines, 0 classes, 5 functions)

### `core/`
- `core/__init__.py` (2 lines, 0 classes, 0 functions)
- `core/answer_check.py` (123 lines, 1 classes, 6 functions) — core/answer_check.py -- self-check pass and confidence flagging (roadmap #53, #55).
- `core/archive_safety.py` (21 lines, 0 classes, 1 functions)
- `core/audit_log.py` (266 lines, 0 classes, 15 functions) — core/audit_log.py - searchable record of every gated tool call and decision (roadmap #44).
- `core/chat_commands.py` (322 lines, 1 classes, 0 functions) — core/chat_commands.py — Aether Built-in Chat Commands
- `core/chat_history.py` (290 lines, 1 classes, 9 functions) — Bounded, atomic local storage for named chat transcripts.
- `core/config_home.py` (206 lines, 0 classes, 14 functions) — core/config_home.py - where Aether's settings live, and loading them safely.
- `core/config_io.py` (100 lines, 0 classes, 4 functions) — core/config_io.py -- save the settings dialog's values WITHOUT destroying the rest.
- `core/config_validator.py` (201 lines, 2 classes, 9 functions) — core/config_validator.py — Aether Configuration Validator
- `core/conversation_core.py` (1428 lines, 1 classes, 10 functions) — core/conversation_core.py — Aether Conversation Core
- `core/crash_logger.py` (218 lines, 0 classes, 6 functions) — core/crash_logger.py — Aether Crash Recovery Logging
- `core/credentials.py` (149 lines, 0 classes, 11 functions) — core/credentials.py -- one place to ask "what is the API key for X?".
- `core/data_home.py` (206 lines, 0 classes, 14 functions) — Stable per-user storage for Aether's persistent data.
- `core/degrade.py` (96 lines, 2 classes, 2 functions) — core/degrade.py - graceful degradation when the model backend fails (roadmap #42).
- `core/device_specs.py` (149 lines, 2 classes, 4 functions) — device_specs.py — best-effort local hardware detection + Ollama model
- `core/diagnostics.py` (135 lines, 1 classes, 2 functions) — diagnostics.py — Aether System Diagnostics
- `core/doctor.py` (434 lines, 1 classes, 25 functions) — core/doctor.py -- `/doctor`: find what is wrong with an Aether install and say how to fix it.
- `core/echo.py` (388 lines, 2 classes, 5 functions) — Telling the user's voice apart from our own coming back through the speakers.
- `core/evaluator.py` (130 lines, 2 classes, 0 functions) — core/evaluator.py — Aether Self-Evaluation System
- `core/file_watcher.py` (126 lines, 1 classes, 1 functions) — core/file_watcher.py — Aether File Watcher
- `core/goal_state.py` (310 lines, 5 classes, 0 functions) — core/goal_state.py — Aether Goal & State Manager
- `core/headless_ui.py` (218 lines, 2 classes, 2 functions) — headless_ui.py — Headless drop-in replacement for ui.py's AetherUI.
- `core/hf_offline.py` (36 lines, 0 classes, 1 functions) — core/hf_offline.py -- temporary network access for first-run model downloads.
- `core/hotkey_manager.py` (61 lines, 1 classes, 0 functions) — hotkey_commands.py — Aether Global Hotkey Manager (stub)
- `core/installer.py` (185 lines, 0 classes, 4 functions) — Aether — Dependency auto-installer.
- `core/known_folders.py` (66 lines, 0 classes, 2 functions) — core/known_folders.py -- real user folder locations on Windows.
- `core/leak_guard.py` (173 lines, 1 classes, 8 functions) — core/leak_guard.py - keep secrets out of requests to cloud LLMs (roadmap #43).
- `core/listen_loops.py` (376 lines, 2 classes, 0 functions) — core/listen_loops.py — AetherLocal's microphone/STT background loops.
- `core/listen_mode.py` (166 lines, 1 classes, 4 functions) — core/listen_mode.py - which gate sits in front of speech-to-text (roadmap #95).
- `core/live_report.py` (211 lines, 0 classes, 6 functions) — core/live_report.py — Live per-turn usage dump.
- `core/llm_client.py` (1571 lines, 0 classes, 41 functions) — Local LLM client for Aether.
- `core/loop_guard.py` (85 lines, 2 classes, 0 functions) — core/loop_guard.py — Aether Loop Guard
- `core/mcp_bridge.py` (317 lines, 2 classes, 1 functions) — mcp_bridge.py — Aether v4.1 MCP (Model Context Protocol) Bridge
- `core/memory_hygiene.py` (48 lines, 0 classes, 4 functions) — core/memory_hygiene.py -- keep failed turns out of long-term memory.
- `core/memory_tools.py` (383 lines, 0 classes, 5 functions) — memory/memory_tools.py — Aether Memory Backup, Export, Import & Pruning
- `core/notify.py` (129 lines, 0 classes, 3 functions) — notify.py — Push notifications (ntfy.sh, or a self-hosted ntfy instance)
- `core/observability.py` (324 lines, 5 classes, 1 functions) — core/observability.py — Aether Observability Layer
- `core/pending_approvals.py` (129 lines, 2 classes, 1 functions) — pending_approvals.py — In-memory store for headless remote-approval requests.
- `core/perf_stats.py` (229 lines, 0 classes, 12 functions) — core/perf_stats.py - speed, memory and cloud-usage numbers per model (roadmap #47).
- `core/plugin_watcher.py` (108 lines, 0 classes, 1 functions) — plugin_watcher.py — Aether v4.1 Plugin Hot-Reload
- `core/plugins.py` (218 lines, 1 classes, 1 functions) — plugins.py - Aether plugin system.
- `core/portable_bundle.py` (416 lines, 0 classes, 13 functions) — Portable, secret-free export and restore for Aether's local user data.
- `core/prompt_builder.py` (188 lines, 1 classes, 2 functions) — core/prompt_builder.py — Aether System Prompt Assembly
- `core/protected_paths.py` (53 lines, 0 classes, 1 functions) — core/protected_paths.py -- files the model must never be able to read through any tool.
- `core/quick_intents.py` (169 lines, 0 classes, 4 functions) — core/quick_intents.py -- run simple voice commands without asking the LLM.
- `core/resource_governor.py` (154 lines, 2 classes, 3 functions) — core/resource_governor.py - back off when the machine is busy (roadmap #46).
- `core/response_cache.py` (279 lines, 1 classes, 11 functions) — core/response_cache.py - roadmap #54: answer a repeated question without the model.
- `core/risk.py` (451 lines, 0 classes, 18 functions) — core/risk.py -- one declarative table of what each tool/action is allowed to do.
- `core/safety.py` (118 lines, 2 classes, 2 functions) — safety.py — Aether v4.1 Safety & Verification Layer
- `core/self_test.py` (464 lines, 2 classes, 22 functions) — core/self_test.py — Aether Self-Test Mode
- `core/smart_context.py` (152 lines, 0 classes, 3 functions) — smart_context.py — Aether v3 Context Compression (stub / implementation)
- `core/stt.py` (145 lines, 2 classes, 0 functions) — Speech-to-Text engines for Aether.
- `core/task_scheduler.py` (125 lines, 2 classes, 0 functions) — task_scheduler.py — Aether v3 Task Scheduler (stub / minimal implementation)
- `core/tier_router.py` (181 lines, 2 classes, 6 functions) — Tier-based LLM routing for Aether.
- `core/tool_declarations.py` (589 lines, 0 classes, 3 functions) — core/tool_declarations.py — Aether's built-in tool schema.
- `core/tool_dispatch.py` (495 lines, 1 classes, 0 functions) — core/tool_dispatch.py — Aether Tool Dispatch
- `core/tool_router.py` (144 lines, 0 classes, 5 functions) — tool_router.py — per-turn tool subset selection for EVERYDAY-tier turns.
- `core/trace_log.py` (1028 lines, 7 classes, 37 functions) — core/trace_log.py - the flight recorder: what Aether did, when, and with what.
- `core/tts.py` (462 lines, 4 classes, 6 functions) — Text-to-Speech engines for Aether.
- `core/undo.py` (103 lines, 2 classes, 4 functions) — core/undo.py — Aether Undo Stack
- `core/wake_word.py` (269 lines, 2 classes, 3 functions) — Local wake-word detection for JARVIS ("Hey Jarvis").

### `dashboard/`
- `dashboard/__init__.py` (1 lines, 0 classes, 0 functions)
- `dashboard/__main__.py` (50 lines, 0 classes, 2 functions) — Standalone dashboard: ``python -m dashboard``.
- `dashboard/api_secure.py` (126 lines, 2 classes, 4 functions) — dashboard/api_secure.py — Stage 2 of the remote-access build (see HANDOFF.md #3).
- `dashboard/openai_api.py` (187 lines, 1 classes, 4 functions) — dashboard/openai_api.py - OpenAI-compatible endpoint for Aether's models (roadmap #41).
- `dashboard/server.py` (1273 lines, 2 classes, 15 functions) — dashboard/server.py — AETHER Local Dashboard

### `dev_tools/`
- `dev_tools/dump.py` (78 lines, 0 classes, 1 functions) — dump.py — MARK XL Codebase Exporter

### `integrations/`
- `integrations/__init__.py` (14 lines, 0 classes, 0 functions) — integrations/ — Aether v3 external framework integrations.
- `integrations/langchain_tools.py` (497 lines, 0 classes, 15 functions) — integrations/langchain_tools.py — Aether × LangChain
- `integrations/llama_index_rag.py` (366 lines, 0 classes, 13 functions) — integrations/llama_index_rag.py — Aether × LlamaIndex
- `integrations/oi_shell.py` (267 lines, 0 classes, 10 functions) — integrations/oi_shell.py — Aether × Open Interpreter

### `memory/`
- `memory/__init__.py` (1 lines, 0 classes, 0 functions)
- `memory/config_manager.py` (171 lines, 1 classes, 8 functions)
- `memory/hybrid_memory.py` (417 lines, 3 classes, 1 functions) — memory/hybrid_memory.py — Aether Hybrid Memory
- `memory/memory_manager.py` (222 lines, 0 classes, 12 functions)
- `memory/rag.py` (221 lines, 2 classes, 0 functions) — memory/rag.py — Aether Hybrid RAG Engine
- `memory/semantic_memory.py` (210 lines, 2 classes, 0 functions) — memory_engine.py — Aether v3 Memory Engine

### `plugins/`
- `plugins/_template.py` (44 lines, 0 classes, 1 functions) — Drop-in Aether plugin template.

### `security/`
- `security/__init__.py` (44 lines, 0 classes, 0 functions) — security — Aether remote-access security layer (v4.1+).
- `security/audit.py` (156 lines, 0 classes, 5 functions) — security/audit.py — append-only security event log.
- `security/capabilities.py` (121 lines, 2 classes, 2 functions) — security/capabilities.py — gated actions + risk levels.
- `security/first_run.py` (144 lines, 0 classes, 4 functions) — security/first_run.py — first-time setup, run from Aether's desktop UI.
- `security/middleware.py` (211 lines, 0 classes, 7 functions) — security/middleware.py — FastAPI dependencies for capability / session checks.
- `security/policy.py` (191 lines, 2 classes, 2 functions) — security/policy.py — runtime approval flow for HIGH-risk capabilities.
- `security/tokens.py` (473 lines, 3 classes, 1 functions) — security/tokens.py — SecurityManager: state + token issuance + device pairing.

### `tests/`
- `tests/conftest.py` (99 lines, 0 classes, 5 functions) — pytest configuration for tests/.
- `tests/stress_test.py` (369 lines, 0 classes, 15 functions) — stress_test.py — Aether Comprehensive Stress Test
- `tests/test_action_launcher.py` (304 lines, 0 classes, 5 functions)
- `tests/test_action_signatures.py` (44 lines, 0 classes, 4 functions) — Roadmap #16: the dead `session_memory` parameter is gone from actions/,
- `tests/test_all_critical.py` (2166 lines, 0 classes, 90 functions)
- `tests/test_answer_check.py` (194 lines, 1 classes, 25 functions) — Roadmap #53 (self-check pass) and #55 (confidence / 'I don't know' flagging).
- `tests/test_apply_handoff.py` (66 lines, 0 classes, 6 functions) — tools/apply_handoff_v12.py records the session once, through the handoff module.
- `tests/test_apply_roadmap.py` (97 lines, 0 classes, 8 functions) — tools/apply_roadmap_v12.py must add items through the roadmap module, idempotently.
- `tests/test_audit_log.py` (165 lines, 0 classes, 16 functions)
- `tests/test_chat_commands.py` (117 lines, 0 classes, 5 functions)
- `tests/test_chat_history.py` (127 lines, 0 classes, 9 functions)
- `tests/test_config_home.py` (96 lines, 0 classes, 10 functions) — core/config_home: stable per-user config location, one-time migration, typo survival.
- `tests/test_config_io.py` (159 lines, 1 classes, 17 functions) — Saving the settings dialog must not delete settings it does not show, and must keep
- `tests/test_core.py` (90 lines, 1 classes, 7 functions)
- `tests/test_crash_reporter.py` (87 lines, 0 classes, 9 functions) — Roadmap #49: crash reports are sanitized, self-contained and linked to the trace.
- `tests/test_credentials.py` (335 lines, 1 classes, 32 functions) — core/credentials.py + tools/secrets_cli.py: env > OS keyring > config file, never logged,
- `tests/test_dangerous_confirmation.py` (74 lines, 0 classes, 7 functions) — Shutdown/restart must be approved by the USER (UI dialog), never by a model-supplied
- `tests/test_dashboard_security.py` (204 lines, 0 classes, 17 functions) — Dashboard: encrypt-then-MAC, random session keys, login lockout (roadmap #33/#34).
- `tests/test_dashboard_standalone.py` (37 lines, 0 classes, 2 functions) — Roadmap #21: the dashboard can be built and queried without AetherLocal.
- `tests/test_dashboard_wiring.py` (151 lines, 1 classes, 12 functions) — The web dashboard must be usable: a way to get a pairing code, replies that reach the
- `tests/test_data_home.py` (115 lines, 0 classes, 7 functions)
- `tests/test_degrade.py` (90 lines, 0 classes, 11 functions)
- `tests/test_dep_watcher.py` (123 lines, 1 classes, 9 functions) — Tests for tools/maintenance/dep_watcher.py upstream lookups (roadmap #31).
- `tests/test_desktop_sandbox.py` (229 lines, 0 classes, 16 functions) — Roadmap #23: the desktop task sandbox must not be escapable, and model-
- `tests/test_desktop_timeout.py` (121 lines, 0 classes, 14 functions) — Approved desktop code runs in a child process with a deadline: `while True: pass`
- `tests/test_doctor.py` (279 lines, 0 classes, 35 functions) — core/doctor.py: every check against a throwaway install; no network, no Ollama.
- `tests/test_document_citations.py` (140 lines, 2 classes, 5 functions)
- `tests/test_eval_harness.py` (376 lines, 0 classes, 29 functions) — tools/eval_harness.py + the AETHER_EVAL_OVERRIDES hook: no model, no Aether process needed.
- `tests/test_except_sweep_actions.py` (19 lines, 0 classes, 1 functions) — Roadmap #10: failures in actions/ must not look like empty/negative results.
- `tests/test_execution_gates.py` (293 lines, 0 classes, 22 functions) — Tools that execute model-written or model-chosen code must be approved by the USER
- `tests/test_invariants.py` (675 lines, 1 classes, 51 functions) — Whole-codebase invariants: properties that must hold across ALL tools / ALL inputs.
- `tests/test_leak_guard.py` (110 lines, 0 classes, 13 functions)
- `tests/test_listen_mode.py` (355 lines, 2 classes, 29 functions) — Roadmap #95: listen modes / push-to-talk so background media is never transcribed.
- `tests/test_live_grader.py` (151 lines, 0 classes, 19 functions) — The grader must FAIL on the real bad replies seen in logs and PASS on the good ones.
- `tests/test_maintenance_publish.py` (39 lines, 0 classes, 2 functions)
- `tests/test_mark_lv_ports.py` (336 lines, 2 classes, 26 functions) — Tests for the MARK LV ports: EchoGuard/OutputTap, WakeGate, Gemini opt-in route.
- `tests/test_mcp_bridge.py` (234 lines, 0 classes, 11 functions)
- `tests/test_memory_empty_values.py` (23 lines, 0 classes, 2 functions) — Roadmap #32: empty/None values wrapped in {"value": ...} must not be stored.
- `tests/test_notify_followup.py` (227 lines, 0 classes, 18 functions) — Roadmap #19 / #20: ntfy follow-through for approvals and the audit trail
- `tests/test_openai_api.py` (123 lines, 0 classes, 11 functions)
- `tests/test_osint_tools.py` (209 lines, 2 classes, 8 functions)
- `tests/test_pending_approvals.py` (23 lines, 0 classes, 2 functions)
- `tests/test_perf_stats.py` (202 lines, 0 classes, 18 functions)
- `tests/test_plugin_trust.py` (37 lines, 0 classes, 1 functions)
- `tests/test_portable_bundle.py` (139 lines, 0 classes, 4 functions)
- `tests/test_promise_retry.py` (48 lines, 0 classes, 3 functions) — Roadmap #92: a reply that only promises an action ('I will research...') is
- `tests/test_promise_without_action.py` (180 lines, 0 classes, 11 functions) — Roadmap #92: small models that announce an action but never call a tool.
- `tests/test_prompt_budget.py` (156 lines, 0 classes, 14 functions) — Roadmap #93: keep the per-turn prompt small.
- `tests/test_protected_paths.py` (233 lines, 0 classes, 16 functions) — Secrets files must not be readable through any tool; port scans are bounded.
- `tests/test_quick_intents.py` (195 lines, 0 classes, 15 functions) — Quick-intent shortcuts: the 15 live prompts plus false-positive guards.
- `tests/test_req_check.py` (79 lines, 0 classes, 6 functions) — verify_local must flag packages that requirements.txt declares but are missing.
- `tests/test_resource_governor.py` (111 lines, 1 classes, 10 functions)
- `tests/test_response_cache.py` (238 lines, 0 classes, 17 functions) — Roadmap #54: opt-in response cache (core/response_cache.py + its wiring in the turn loop).
- `tests/test_risk.py` (364 lines, 0 classes, 33 functions) — core/risk.py: one declarative tier table, enforced at the entry function of every tool
- `tests/test_risk_task.py` (260 lines, 2 classes, 24 functions) — The TASK tier: ask once per user request (quoting the user), cover that tool for that
- `tests/test_routing_and_paths.py` (210 lines, 0 classes, 16 functions) — Regression tests for tier routing, known folders, weather and open_app honesty.
- `tests/test_security_fixes.py` (61 lines, 0 classes, 5 functions) — calculate() must not allow attribute/dunder escapes or runaway powers; open_app must not shell out.
- `tests/test_server_mode.py` (58 lines, 0 classes, 6 functions)
- `tests/test_shell_safety.py` (166 lines, 0 classes, 15 functions) — Regression tests for roadmap #29: no shell=True call sites remain.
- `tests/test_stream_cancellation.py` (154 lines, 0 classes, 4 functions)
- `tests/test_tool_call_recovery.py` (101 lines, 0 classes, 12 functions) — Recovery of raw <tool_call> text, held-back streaming, plain monitor alerts, HF offline restore.
- `tests/test_trace_log.py` (174 lines, 0 classes, 15 functions) — Flight recorder (core/trace_log.py) and its viewer (tools/trace_view.py).
- `tests/test_tts_elevenlabs.py` (74 lines, 0 classes, 6 functions)
- `tests/test_v12e_fixes.py` (113 lines, 0 classes, 8 functions) — Regression tests for the v12d review fixes (each fails against v12d).
- `tests/test_v12f_fixes.py` (40 lines, 0 classes, 5 functions) — v12f: LangChain must not pretend to have researched; live log isolation.
- `tests/test_v12g_fixes.py` (122 lines, 1 classes, 11 functions) — v12g: a missing Ollama model / missing config must fail loudly and early, not as 24 silent 404s.
- `tests/test_voice_browser_fixes.py` (197 lines, 4 classes, 16 functions) — Voice + browser regressions from real-hardware logs (clipped words, background audio
- `tests/test_web_search.py` (140 lines, 0 classes, 7 functions)

### `tools/`
- `tools/__init__.py` (1 lines, 0 classes, 0 functions)
- `tools/apply_handoff_v12.py` (171 lines, 0 classes, 2 functions) — Record the v12 session in the handoff log through the project's own handoff code.
- `tools/apply_roadmap_v12.py` (113 lines, 0 classes, 3 functions) — Add the v12 backlog items through the project's own roadmap code (never hand-edit).
- `tools/clean_memory.py` (91 lines, 0 classes, 2 functions) — Find and remove junk rows from Aether's exchange memory database.
- `tools/eval_harness.py` (487 lines, 0 classes, 16 functions) — tools/eval_harness.py -- score Aether on fixed prompts, per model, and compare runs.
- `tools/fix_local_state.py` (104 lines, 0 classes, 3 functions) — Clean up local state damaged by the old prompt.txt few-shot examples.
- `tools/live_grader.py` (238 lines, 0 classes, 11 functions) — tools/live_grader.py -- grade a headless replay against tests/live_test_cases.json.
- `tools/maintenance/__init__.py` (58 lines, 0 classes, 2 functions) — Aether Maintenance Toolkit
- `tools/maintenance/dep_watcher.py` (269 lines, 0 classes, 11 functions) — Checks Aether's dependencies against their upstream sources and writes
- `tools/maintenance/git_setup.py` (78 lines, 0 classes, 2 functions) — One-time helper for getting this checkout properly connected to
- `tools/maintenance/handoff.py` (138 lines, 0 classes, 7 functions) — Session handoff log.
- `tools/maintenance/health_check.py` (201 lines, 0 classes, 6 functions) — Scans the codebase for common maintenance smells and writes
- `tools/maintenance/project_state.py` (172 lines, 0 classes, 6 functions) — Generates PROJECT_STATE.md — a single, always-current snapshot of the
- `tools/maintenance/publish.py` (106 lines, 0 classes, 5 functions) — Commits the generated maintenance reports (and roadmap.json) to git and
- `tools/maintenance/roadmap.py` (221 lines, 0 classes, 12 functions) — Roadmap / backlog tracker.
- `tools/maintenance/run_all.py` (103 lines, 0 classes, 1 functions) — Runs every maintenance check and regenerates all reports in one shot:
- `tools/req_check.py` (102 lines, 0 classes, 5 functions) — tools/req_check.py -- which packages declared in requirements.txt are not installed?
- `tools/secrets_cli.py` (159 lines, 0 classes, 7 functions) — tools/secrets_cli.py -- keep API keys in the OS credential store instead of a JSON file.
- `tools/trace_view.py` (203 lines, 0 classes, 12 functions) — python -m tools.trace_view - read the flight recorder (logs/trace/*.jsonl).
- `tools/verify_local.py` (355 lines, 1 classes, 11 functions) — tools/verify_local.py -- one-shot health check for a real Aether install.

## Recent history (git log)
- 9fdeada 2026-10-30 Roadmap #49: sanitized, trace-linked crash reports
- da2589d 2026-10-30 Add flight-recorder logging: full per-event trace of every run
- af86867 2026-10-29 Roadmap #53/#55: opt-in self-check pass and confidence flagging

## See also
- `HEALTH_REPORT.md` — automated code health scan
- `DEPENDENCY_REPORT.md` — outdated / risky dependency scan
- `ROADMAP.md` — hand-curated backlog (edit via `tools/maintenance/roadmap.py`)
