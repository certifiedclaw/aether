# Aether — Project State

> **Auto-generated.** Do not hand-edit — rerun `python -m tools.maintenance.project_state` (or `run_all`) to refresh. This is meant to be the one file a new session (human or Claude) reads first to get oriented.

Generated: 2026-10-04 21:26 UTC
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
| `./` | 12 | 5,474 |
| `actions/` | 22 | 10,758 |
| `agent/` | 4 | 1,144 |
| `config/` | 1 | 27 |
| `core/` | 36 | 8,022 |
| `dashboard/` | 3 | 1,093 |
| `dev_tools/` | 1 | 78 |
| `integrations/` | 4 | 997 |
| `memory/` | 6 | 1,172 |
| `plugins/` | 1 | 44 |
| `security/` | 7 | 1,338 |
| `tests/` | 4 | 2,486 |
| `tools/` | 10 | 1,240 |
| **Total** | **111** | **33,873** |

## Modules with docstrings (top-level summary)

### `actions/`
- `actions\browser_control.py` (911 lines, 2 classes, 10 functions)
- `actions\code_helper.py` (520 lines, 0 classes, 21 functions)
- `actions\computer_control.py` (508 lines, 0 classes, 22 functions)
- `actions\computer_settings.py` (673 lines, 0 classes, 61 functions)
- `actions\desktop.py` (456 lines, 0 classes, 13 functions)
- `actions\dev_agent.py` (513 lines, 1 classes, 15 functions)
- `actions\extra_tools.py` (1006 lines, 0 classes, 46 functions) — extra_tools.py — Aether v3 Extended Capabilities
- `actions\file_controller.py` (618 lines, 0 classes, 26 functions)
- `actions\file_processor.py` (834 lines, 0 classes, 15 functions) — file_processor.py — Aether Universal File Processor
- `actions\flight_finder.py` (347 lines, 0 classes, 9 functions)
- `actions\game_updater.py` (1048 lines, 0 classes, 39 functions)
- `actions\open_app.py` (257 lines, 0 classes, 5 functions)
- `actions\osint_tools.py` (1185 lines, 0 classes, 22 functions) — osint_tools.py — Aether v3 OSINT Toolkit
- `actions\pdf_tools.py` (110 lines, 0 classes, 6 functions) — pdf_tools.py — Aether v3 PDF Tools (PyMuPDF)
- `actions\proactive.py` (117 lines, 1 classes, 0 functions) — ProactiveEngine 2.0 - context-aware, time-aware, non-repetitive background prompts.
- `actions\reminder.py` (332 lines, 0 classes, 9 functions)
- `actions\screen_processor.py` (273 lines, 0 classes, 13 functions) — Aether — Screen / Camera Processor
- `actions\send_message.py` (266 lines, 0 classes, 17 functions)
- `actions\system_monitor.py` (201 lines, 1 classes, 4 functions) — System Monitor — background metric checks with voice alert support.
- `actions\weather_report.py` (51 lines, 0 classes, 2 functions)
- `actions\web_search.py` (118 lines, 0 classes, 6 functions)
- `actions\youtube_video.py` (414 lines, 0 classes, 16 functions)

### `agent/`
- `agent\error_handler.py` (150 lines, 1 classes, 3 functions) — Aether — Error Handler
- `agent\executor.py` (438 lines, 1 classes, 6 functions) — Aether — Agent Executor
- `agent\planner.py` (305 lines, 0 classes, 5 functions) — Aether — Task Planner
- `agent\task_queue.py` (251 lines, 4 classes, 1 functions)

### `config/`
- `config\__init__.py` (27 lines, 0 classes, 5 functions)

### `core/`
- `core\__init__.py` (2 lines, 0 classes, 0 functions)
- `core\chat_commands.py` (128 lines, 1 classes, 0 functions) — core/chat_commands.py — Aether Built-in Chat Commands
- `core\config_validator.py` (201 lines, 2 classes, 9 functions) — core/config_validator.py — Aether Configuration Validator
- `core\conversation_core.py` (662 lines, 1 classes, 1 functions) — core/conversation_core.py — Aether Conversation Core
- `core\crash_logger.py` (141 lines, 0 classes, 4 functions) — core/crash_logger.py — Aether Crash Recovery Logging
- `core\device_specs.py` (149 lines, 2 classes, 4 functions) — device_specs.py — best-effort local hardware detection + Ollama model
- `core\diagnostics.py` (135 lines, 1 classes, 2 functions) — diagnostics.py — Aether System Diagnostics
- `core\evaluator.py` (130 lines, 2 classes, 0 functions) — core/evaluator.py — Aether Self-Evaluation System
- `core\file_watcher.py` (126 lines, 1 classes, 1 functions) — core/file_watcher.py — Aether File Watcher
- `core\goal_state.py` (308 lines, 5 classes, 0 functions) — core/goal_state.py — Aether Goal & State Manager
- `core\headless_ui.py` (165 lines, 2 classes, 1 functions) — headless_ui.py — Headless drop-in replacement for ui.py's AetherUI.
- `core\hotkey_manager.py` (61 lines, 1 classes, 0 functions) — hotkey_commands.py — Aether Global Hotkey Manager (stub)
- `core\installer.py` (158 lines, 0 classes, 3 functions) — Aether — Dependency auto-installer.
- `core\listen_loops.py` (211 lines, 2 classes, 0 functions) — core/listen_loops.py — AetherLocal's microphone/STT background loops.
- `core\live_report.py` (211 lines, 0 classes, 6 functions) — core/live_report.py — Live per-turn usage dump.
- `core\llm_client.py` (896 lines, 0 classes, 16 functions) — Local LLM client for Aether.
- `core\loop_guard.py` (85 lines, 2 classes, 0 functions) — core/loop_guard.py — Aether Loop Guard
- `core\mcp_bridge.py` (212 lines, 2 classes, 0 functions) — mcp_bridge.py — Aether v4.1 MCP (Model Context Protocol) Bridge
- `core\memory_tools.py` (379 lines, 0 classes, 5 functions) — memory/memory_tools.py — Aether Memory Backup, Export, Import & Pruning
- `core\notify.py` (54 lines, 0 classes, 1 functions) — notify.py — Push notifications (ntfy.sh, or a self-hosted ntfy instance)
- `core\observability.py` (324 lines, 5 classes, 1 functions) — core/observability.py — Aether Observability Layer
- `core\pending_approvals.py` (122 lines, 2 classes, 1 functions) — pending_approvals.py — In-memory store for headless remote-approval requests.
- `core\plugin_watcher.py` (108 lines, 0 classes, 1 functions) — plugin_watcher.py — Aether v4.1 Plugin Hot-Reload
- `core\plugins.py` (201 lines, 1 classes, 1 functions) — plugins.py - Aether plugin system.
- `core\prompt_builder.py` (110 lines, 1 classes, 0 functions) — core/prompt_builder.py — Aether System Prompt Assembly
- `core\safety.py` (106 lines, 2 classes, 1 functions) — safety.py — Aether v4.1 Safety & Verification Layer
- `core\self_test.py` (471 lines, 2 classes, 22 functions) — core/self_test.py — Aether Self-Test Mode
- `core\smart_context.py` (149 lines, 0 classes, 3 functions) — smart_context.py — Aether v3 Context Compression (stub / implementation)
- `core\stt.py` (81 lines, 2 classes, 0 functions) — Speech-to-Text engines for Aether.
- `core\task_scheduler.py` (105 lines, 2 classes, 0 functions) — task_scheduler.py — Aether v3 Task Scheduler (stub / minimal implementation)
- `core\tier_router.py` (164 lines, 2 classes, 5 functions) — Tier-based LLM routing for Aether.
- `core\tool_declarations.py` (571 lines, 0 classes, 3 functions) — core/tool_declarations.py — Aether's built-in tool schema.
- `core\tool_dispatch.py` (459 lines, 1 classes, 0 functions) — core/tool_dispatch.py — Aether Tool Dispatch
- `core\tool_router.py` (103 lines, 0 classes, 2 functions) — tool_router.py — per-turn tool subset selection for EVERYDAY-tier turns.
- `core\tts.py` (431 lines, 4 classes, 6 functions) — Text-to-Speech engines for Aether.
- `core\undo.py` (103 lines, 2 classes, 4 functions) — core/undo.py — Aether Undo Stack

### `dashboard/`
- `dashboard\__init__.py` (1 lines, 0 classes, 0 functions)
- `dashboard\api_secure.py` (126 lines, 2 classes, 4 functions) — dashboard/api_secure.py — Stage 2 of the remote-access build (see HANDOFF.md #3).
- `dashboard\server.py` (966 lines, 1 classes, 12 functions) — dashboard/server.py — AETHER Local HTTP Dashboard

### `dev_tools/`
- `dev_tools\dump.py` (78 lines, 0 classes, 1 functions) — dump.py — MARK XL Codebase Exporter

### `integrations/`
- `integrations\__init__.py` (14 lines, 0 classes, 0 functions) — integrations/ — Aether v3 external framework integrations.
- `integrations\langchain_tools.py` (377 lines, 0 classes, 9 functions) — integrations/langchain_tools.py — Aether × LangChain
- `integrations\llama_index_rag.py` (339 lines, 0 classes, 12 functions) — integrations/llama_index_rag.py — Aether × LlamaIndex
- `integrations\oi_shell.py` (267 lines, 0 classes, 10 functions) — integrations/oi_shell.py — Aether × Open Interpreter

### `memory/`
- `memory\__init__.py` (1 lines, 0 classes, 0 functions)
- `memory\config_manager.py` (165 lines, 1 classes, 8 functions)
- `memory\hybrid_memory.py` (403 lines, 3 classes, 0 functions) — memory/hybrid_memory.py — Aether Hybrid Memory
- `memory\memory_manager.py` (215 lines, 0 classes, 12 functions)
- `memory\rag.py` (179 lines, 2 classes, 0 functions) — memory/rag.py — Aether Hybrid RAG Engine
- `memory\semantic_memory.py` (209 lines, 2 classes, 0 functions) — memory_engine.py — Aether v3 Memory Engine

### `plugins/`
- `plugins\_template.py` (44 lines, 0 classes, 1 functions) — Drop-in Aether plugin template.

### `security/`
- `security\__init__.py` (44 lines, 0 classes, 0 functions) — security — Aether remote-access security layer (v4.1+).
- `security\audit.py` (156 lines, 0 classes, 5 functions) — security/audit.py — append-only security event log.
- `security\capabilities.py` (121 lines, 2 classes, 2 functions) — security/capabilities.py — gated actions + risk levels.
- `security\first_run.py` (143 lines, 0 classes, 4 functions) — security/first_run.py — first-time setup, run from Aether's desktop UI.
- `security\middleware.py` (211 lines, 0 classes, 7 functions) — security/middleware.py — FastAPI dependencies for capability / session checks.
- `security\policy.py` (191 lines, 2 classes, 2 functions) — security/policy.py — runtime approval flow for HIGH-risk capabilities.
- `security\tokens.py` (472 lines, 3 classes, 1 functions) — security/tokens.py — SecurityManager: state + token issuance + device pairing.

### `tests/`
- `tests\conftest.py` (17 lines, 0 classes, 0 functions) — pytest configuration for tests/.
- `tests\stress_test.py` (369 lines, 0 classes, 15 functions) — stress_test.py — Aether Comprehensive Stress Test
- `tests\test_all_critical.py` (2010 lines, 0 classes, 85 functions)
- `tests\test_core.py` (90 lines, 1 classes, 7 functions)

### `tools/`
- `tools\__init__.py` (1 lines, 0 classes, 0 functions)
- `tools\maintenance\__init__.py` (58 lines, 0 classes, 2 functions) — Aether Maintenance Toolkit
- `tools\maintenance\dep_watcher.py` (183 lines, 0 classes, 7 functions) — Checks Aether's dependencies against their upstream sources and writes
- `tools\maintenance\git_setup.py` (78 lines, 0 classes, 2 functions) — One-time helper for getting this checkout properly connected to
- `tools\maintenance\handoff.py` (138 lines, 0 classes, 7 functions) — Session handoff log.
- `tools\maintenance\health_check.py` (201 lines, 0 classes, 6 functions) — Scans the codebase for common maintenance smells and writes
- `tools\maintenance\project_state.py` (172 lines, 0 classes, 6 functions) — Generates PROJECT_STATE.md — a single, always-current snapshot of the
- `tools\maintenance\publish.py` (106 lines, 0 classes, 5 functions) — Commits the generated maintenance reports (and roadmap.json) to git and
- `tools\maintenance\roadmap.py` (200 lines, 0 classes, 11 functions) — Roadmap / backlog tracker.
- `tools\maintenance\run_all.py` (103 lines, 0 classes, 1 functions) — Runs every maintenance check and regenerates all reports in one shot:

## Recent history (git log)
_No git history available in this checkout._

## See also
- `HEALTH_REPORT.md` — automated code health scan
- `DEPENDENCY_REPORT.md` — outdated / risky dependency scan
- `ROADMAP.md` — hand-curated backlog (edit via `tools/maintenance/roadmap.py`)
