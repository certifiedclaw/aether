# Aether — Project State

> **Auto-generated.** Do not hand-edit — rerun `python -m tools.maintenance.project_state` (or `run_all`) to refresh. This is meant to be the one file a new session (human or Claude) reads first to get oriented.

Generated: 2026-07-13 18:07 UTC
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
| `./` | 11 | 6,311 |
| `actions/` | 22 | 10,620 |
| `agent/` | 4 | 1,119 |
| `config/` | 1 | 27 |
| `core/` | 15 | 2,872 |
| `dashboard/` | 2 | 855 |
| `integrations/` | 4 | 989 |
| `memory/` | 6 | 1,050 |
| `tools/` | 10 | 1,222 |
| **Total** | **75** | **25,065** |

## Modules with docstrings (top-level summary)

### `actions/`
- `actions\browser_control.py` (893 lines, 2 classes, 10 functions)
- `actions\code_helper.py` (520 lines, 0 classes, 21 functions)
- `actions\computer_control.py` (508 lines, 0 classes, 22 functions)
- `actions\computer_settings.py` (673 lines, 0 classes, 61 functions)
- `actions\desktop.py` (456 lines, 0 classes, 13 functions)
- `actions\dev_agent.py` (513 lines, 1 classes, 15 functions)
- `actions\extra_tools.py` (1006 lines, 0 classes, 46 functions) — extra_tools.py — Aether v3 Extended Capabilities
- `actions\file_controller.py` (543 lines, 0 classes, 25 functions)
- `actions\file_processor.py` (834 lines, 0 classes, 15 functions) — file_processor.py — Aether Universal File Processor
- `actions\flight_finder.py` (347 lines, 0 classes, 9 functions)
- `actions\game_updater.py` (1048 lines, 0 classes, 39 functions)
- `actions\open_app.py` (257 lines, 0 classes, 5 functions)
- `actions\osint_tools.py` (1181 lines, 0 classes, 22 functions) — osint_tools.py — Aether v3 OSINT Toolkit
- `actions\pdf_tools.py` (110 lines, 0 classes, 6 functions) — pdf_tools.py — Aether v3 PDF Tools (PyMuPDF)
- `actions\proactive.py` (73 lines, 1 classes, 0 functions) — ProactiveEngine — context-aware background prompting.
- `actions\reminder.py` (332 lines, 0 classes, 9 functions)
- `actions\screen_processor.py` (273 lines, 0 classes, 13 functions) — Aether — Screen / Camera Processor
- `actions\send_message.py` (266 lines, 0 classes, 17 functions)
- `actions\system_monitor.py` (201 lines, 1 classes, 4 functions) — System Monitor — background metric checks with voice alert support.
- `actions\weather_report.py` (51 lines, 0 classes, 2 functions)
- `actions\web_search.py` (118 lines, 0 classes, 6 functions)
- `actions\youtube_video.py` (417 lines, 0 classes, 16 functions)

### `agent/`
- `agent\error_handler.py` (150 lines, 1 classes, 3 functions) — Aether — Error Handler
- `agent\executor.py` (434 lines, 1 classes, 6 functions) — Aether — Agent Executor
- `agent\planner.py` (314 lines, 0 classes, 6 functions) — Aether — Task Planner
- `agent\task_queue.py` (221 lines, 4 classes, 1 functions)

### `config/`
- `config\__init__.py` (27 lines, 0 classes, 5 functions)

### `core/`
- `core\__init__.py` (2 lines, 0 classes, 0 functions)
- `core\diagnostics.py` (114 lines, 1 classes, 2 functions) — diagnostics.py — Aether System Diagnostics
- `core\goal_state.py` (308 lines, 5 classes, 0 functions) — core/goal_state.py — Aether Goal & State Manager
- `core\hotkey_manager.py` (61 lines, 1 classes, 0 functions) — hotkey_commands.py — Aether Global Hotkey Manager (stub)
- `core\installer.py` (158 lines, 0 classes, 3 functions) — Aether — Dependency auto-installer.
- `core\llm_client.py` (704 lines, 0 classes, 11 functions) — Local LLM client for Aether.
- `core\mcp_bridge.py` (212 lines, 2 classes, 0 functions) — mcp_bridge.py — Aether v4.1 MCP (Model Context Protocol) Bridge
- `core\observability.py` (302 lines, 5 classes, 1 functions) — core/observability.py — Aether Observability Layer
- `core\plugin_watcher.py` (108 lines, 0 classes, 1 functions) — plugin_watcher.py — Aether v4.1 Plugin Hot-Reload
- `core\plugins.py` (73 lines, 1 classes, 0 functions) — plugins.py — Aether v3 Plugin System
- `core\safety.py` (106 lines, 2 classes, 1 functions) — safety.py — Aether v4.1 Safety & Verification Layer
- `core\smart_context.py` (128 lines, 0 classes, 2 functions) — smart_context.py — Aether v3 Context Compression (stub / implementation)
- `core\stt.py` (81 lines, 2 classes, 0 functions) — Speech-to-Text engines for Aether.
- `core\task_scheduler.py` (86 lines, 2 classes, 0 functions) — task_scheduler.py — Aether v3 Task Scheduler (stub / minimal implementation)
- `core\tts.py` (429 lines, 4 classes, 6 functions) — Text-to-Speech engines for Aether.

### `dashboard/`
- `dashboard\__init__.py` (1 lines, 0 classes, 0 functions)
- `dashboard\server.py` (854 lines, 1 classes, 9 functions) — dashboard/server.py — AETHER Local HTTP Dashboard

### `integrations/`
- `integrations\__init__.py` (14 lines, 0 classes, 0 functions) — integrations/ — Aether v3 external framework integrations.
- `integrations\langchain_tools.py` (377 lines, 0 classes, 9 functions) — integrations/langchain_tools.py — Aether × LangChain
- `integrations\llama_index_rag.py` (339 lines, 0 classes, 12 functions) — integrations/llama_index_rag.py — Aether × LlamaIndex
- `integrations\oi_shell.py` (259 lines, 0 classes, 10 functions) — integrations/oi_shell.py — Aether × Open Interpreter

### `memory/`
- `memory\__init__.py` (1 lines, 0 classes, 0 functions)
- `memory\config_manager.py` (153 lines, 1 classes, 7 functions)
- `memory\hybrid_memory.py` (345 lines, 3 classes, 0 functions) — memory/hybrid_memory.py — Aether Hybrid Memory
- `memory\memory_manager.py` (215 lines, 0 classes, 12 functions)
- `memory\rag.py` (179 lines, 2 classes, 0 functions) — memory/rag.py — Aether Hybrid RAG Engine
- `memory\semantic_memory.py` (157 lines, 2 classes, 0 functions) — memory_engine.py — Aether v3 Memory Engine

### `tools/`
- `tools\__init__.py` (1 lines, 0 classes, 0 functions)
- `tools\maintenance\__init__.py` (58 lines, 0 classes, 2 functions) — Aether Maintenance Toolkit
- `tools\maintenance\dep_watcher.py` (183 lines, 0 classes, 7 functions) — Checks Aether's dependencies against their upstream sources and writes
- `tools\maintenance\git_setup.py` (78 lines, 0 classes, 2 functions) — One-time helper for getting this checkout properly connected to
- `tools\maintenance\handoff.py` (138 lines, 0 classes, 7 functions) — Session handoff log.
- `tools\maintenance\health_check.py` (183 lines, 0 classes, 5 functions) — Scans the codebase for common maintenance smells and writes
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
