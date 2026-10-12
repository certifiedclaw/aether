# Aether — Health Report

> **Auto-generated.** Rerun `python -m tools.maintenance.health_check` (or `run_all`) to refresh. Fix things here, don't edit this file directly.

Generated: 2026-10-12T21:59:00.951468+00:00
Files scanned: 153

## Summary

| Check | Count |
|---|---|
| Files that fail to parse | 0 |
| Bare `except:` clauses | 0 |
| Files over 600 lines | 15 |
| Functions over 80 lines | 40 |
| Stray `print()` calls (outside scripts) | 224 |
| TODO / FIXME / XXX comments | 0 |
| Unreadable files | 0 |

## Files over 600 lines
- `ui.py` — 2639 lines
- `tests/test_all_critical.py` — 2093 lines
- `actions/osint_tools.py` — 1200 lines
- `core/llm_client.py` — 1183 lines
- `main.py` — 1096 lines
- `actions/extra_tools.py` — 1064 lines
- `actions/game_updater.py` — 1050 lines
- `dashboard/server.py` — 1044 lines
- `actions/browser_control.py` — 913 lines
- `core/conversation_core.py` — 910 lines
- `actions/file_processor.py` — 839 lines
- `actions/computer_settings.py` — 734 lines
- `actions/file_controller.py` — 647 lines
- `tests/test_invariants.py` — 645 lines
- `actions/desktop.py` — 614 lines

## Functions over 80 lines
- `core/conversation_core.py:418` — `_process_message_inner()` (391 lines)
- `core/llm_client.py:1023` — `call_llm_stream()` (160 lines)
- `core/llm_client.py:698` — `_stream_openai_once()` (159 lines)
- `actions/computer_control.py:355` — `computer_control()` (155 lines)
- `actions/dev_agent.py:349` — `_build_project()` (154 lines)
- `ui.py:409` — `paintEvent()` (140 lines)
- `actions/extra_tools.py:924` — `run_extra_tool()` (140 lines)
- `ui.py:1300` — `_build_llm_section()` (139 lines)
- `main.py:956` — `main()` (137 lines)
- `actions/file_processor.py:582` — `_process_video()` (127 lines)
- `agent/executor.py:295` — `execute()` (123 lines)
- `dashboard/server.py:189` — `_ensure_network_access_windows()` (120 lines)
- `main.py:834` — `run()` (115 lines)
- `actions/game_updater.py:928` — `game_updater()` (115 lines)
- `dashboard/server.py:602` — `_register_auth_routes()` (115 lines)
- `core/listen_loops.py:84` — `_listen_whisper()` (111 lines)
- `core/llm_client.py:506` — `call_llm()` (109 lines)
- `actions/browser_control.py:421` — `_launch()` (104 lines)
- `tests/test_all_critical.py:1119` — `test_confirm_blocking_dialog_thread_safety()` (102 lines)
- `actions/reminder.py:41` — `_write_notify_script()` (98 lines)
- `core/smart_context.py:55` — `compress_if_needed()` (96 lines)
- `actions/file_processor.py:313` — `_process_data()` (95 lines)
- `core/echo.py:197` — `is_user_speech()` (94 lines)
- `ui.py:1441` — `_build_tts_section()` (92 lines)
- `core/diagnostics.py:22` — `run_all()` (91 lines)
- `actions/file_processor.py:81` — `_process_image()` (90 lines)
- `ui.py:709` — `__init__()` (88 lines)
- `actions/browser_control.py:818` — `browser_control()` (88 lines)
- `actions/osint_tools.py:903` — `extract_metadata()` (88 lines)
- `core/chat_commands.py:38` — `_handle_chat_command()` (88 lines)
- `actions/code_helper.py:377` — `_screen_debug_action()` (87 lines)
- `actions/computer_settings.py:647` — `computer_settings()` (87 lines)
- `main.py:1004` — `runner()` (86 lines)
- `ui.py:1760` — `__init__()` (85 lines)
- `core/conversation_core.py:202` — `_prepare_conversation_messages()` (85 lines)
- `memory/hybrid_memory.py:291` — `_retrieve()` (85 lines)
- `ui.py:2236` — `_build_right_panel()` (84 lines)
- `ui.py:1884` — `__init__()` (81 lines)
- `actions/osint_tools.py:797` — `github_user_osint()` (81 lines)
- `actions/desktop.py:288` — `set_wallpaper()` (81 lines)

## Stray `print()` calls
Found outside of scripts/tests — consider routing through `core/observability.py` or logging instead.
- `config_validator.py:197`
- `agent/executor.py:77`
- `agent/executor.py:130`
- `agent/executor.py:142`
- `agent/executor.py:145`
- `agent/executor.py:196`
- `agent/executor.py:283`
- `agent/executor.py:301`
- `agent/executor.py:330`
- `agent/executor.py:342`
- `agent/executor.py:348`
- `agent/executor.py:363`
- `agent/executor.py:389`
- `agent/task_queue.py:67`
- `agent/task_queue.py:73`
- `agent/task_queue.py:99`
- `agent/task_queue.py:122`
- `agent/task_queue.py:203`
- `agent/task_queue.py:225`
- `agent/task_queue.py:227`
- `agent/task_queue.py:235`
- `agent/error_handler.py:58`
- `agent/error_handler.py:96`
- `agent/error_handler.py:100`
- `agent/error_handler.py:141`
- `agent/planner.py:82`
- `agent/planner.py:241`
- `agent/planner.py:245`
- `agent/planner.py:247`
- `agent/planner.py:251`
- `agent/planner.py:254`
- `agent/planner.py:259`
- `agent/planner.py:300`
- `agent/planner.py:303`
- `tests/stress_test.py:38`
- `tests/stress_test.py:325`
- `tests/stress_test.py:326`
- `tests/stress_test.py:327`
- `tests/stress_test.py:328`
- `tests/stress_test.py:329`
- `tests/stress_test.py:334`
- `tests/stress_test.py:342`
- `tests/stress_test.py:345`
- `tests/stress_test.py:346`
- `tests/stress_test.py:347`
- `tests/stress_test.py:364`
- `tools/apply_handoff_v12.py:165`
- `tools/live_grader.py:221`
- `tools/live_grader.py:223`
- `tools/apply_roadmap_v12.py:105`
- ...and 174 more
