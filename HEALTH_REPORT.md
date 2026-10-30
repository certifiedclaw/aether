# Aether — Health Report

> **Auto-generated.** Rerun `python -m tools.maintenance.health_check` (or `run_all`) to refresh. Fix things here, don't edit this file directly.

Generated: 2026-10-30T22:19:19.840501+00:00
Files scanned: 204

## Summary

| Check | Count |
|---|---|
| Files that fail to parse | 0 |
| Bare `except:` clauses | 0 |
| Files over 600 lines | 16 |
| Functions over 80 lines | 55 |
| Stray `print()` calls (outside scripts) | 261 |
| TODO / FIXME / XXX comments | 0 |
| Unreadable files | 0 |

## Files over 600 lines
- `ui.py` — 3682 lines
- `tests/test_all_critical.py` — 2165 lines
- `core/llm_client.py` — 1570 lines
- `core/conversation_core.py` — 1427 lines
- `actions/osint_tools.py` — 1355 lines
- `dashboard/server.py` — 1272 lines
- `main.py` — 1196 lines
- `actions/extra_tools.py` — 1077 lines
- `actions/game_updater.py` — 1052 lines
- `core/trace_log.py` — 1027 lines
- `actions/file_processor.py` — 950 lines
- `actions/browser_control.py` — 941 lines
- `actions/computer_settings.py` — 755 lines
- `tests/test_invariants.py` — 674 lines
- `actions/desktop.py` — 655 lines
- `actions/file_controller.py` — 651 lines

## Functions over 80 lines
- `core/conversation_core.py:568` — `_process_message_inner()` (608 lines)
- `ui.py:2453` — `_build_right_panel()` (278 lines)
- `core/chat_commands.py:70` — `_handle_chat_command()` (250 lines)
- `ui.py:2804` — `_open_action_catalog()` (205 lines)
- `core/llm_client.py:1380` — `_call_llm_stream_impl()` (190 lines)
- `core/llm_client.py:960` — `_stream_openai_once()` (186 lines)
- `ui.py:1456` — `_build_llm_section()` (167 lines)
- `core/listen_loops.py:139` — `_listen_whisper()` (158 lines)
- `actions/dev_agent.py:401` — `_build_project()` (154 lines)
- `actions/computer_control.py:354` — `computer_control()` (154 lines)
- `ui.py:3045` — `_open_action_form()` (149 lines)
- `main.py:1048` — `main()` (145 lines)
- `ui.py:428` — `paintEvent()` (140 lines)
- `actions/extra_tools.py:937` — `run_extra_tool()` (140 lines)
- `tests/test_action_launcher.py:10` — `test_action_launcher_builds_form_and_dispatches_values()` (138 lines)
- `main.py:908` — `run()` (133 lines)
- `dashboard/server.py:686` — `_register_auth_routes()` (130 lines)
- `actions/file_processor.py:582` — `_process_video()` (127 lines)
- `actions/osint_tools.py:564` — `tech_stack()` (127 lines)
- `agent/executor.py:310` — `execute()` (127 lines)
- `dashboard/server.py:190` — `_ensure_network_access_windows()` (123 lines)
- `core/conversation_core.py:304` — `_prepare_conversation_messages()` (116 lines)
- `actions/game_updater.py:930` — `game_updater()` (115 lines)
- `tests/test_all_critical.py:760` — `test_setup_overlay_refactor_preserves_behavior()` (115 lines)
- `core/llm_client.py:657` — `_call_llm_impl()` (112 lines)
- `actions/browser_control.py:449` — `_launch()` (104 lines)
- `dashboard/server.py:989` — `_register_file_routes()` (104 lines)
- `tests/test_all_critical.py:1178` — `test_confirm_blocking_dialog_thread_safety()` (102 lines)
- `core/portable_bundle.py:65` — `_write_bundle()` (101 lines)
- `actions/reminder.py:40` — `_write_notify_script()` (98 lines)
- `actions/file_processor.py:711` — `_process_archive()` (97 lines)
- `core/smart_context.py:55` — `compress_if_needed()` (96 lines)
- `actions/file_processor.py:313` — `_process_data()` (95 lines)
- `core/echo.py:197` — `is_user_speech()` (94 lines)
- `core/llm_client.py:1447` — `_do_stream()` (93 lines)
- `ui.py:1625` — `_build_tts_section()` (92 lines)
- `actions/code_helper.py:413` — `_screen_debug_action()` (92 lines)
- `dashboard/openai_api.py:94` — `build_router()` (92 lines)
- `core/diagnostics.py:22` — `run_all()` (91 lines)
- `actions/file_processor.py:81` — `_process_image()` (90 lines)
- `actions/desktop.py:302` — `set_wallpaper()` (89 lines)
- `actions/osint_tools.py:1058` — `extract_metadata()` (88 lines)
- `main.py:1103` — `runner()` (87 lines)
- `actions/browser_control.py:847` — `browser_control()` (87 lines)
- `ui.py:2097` — `__init__()` (86 lines)
- `actions/computer_settings.py:669` — `computer_settings()` (86 lines)
- `actions/desktop.py:569` — `desktop_control()` (86 lines)
- `tools/live_grader.py:132` — `grade_overall()` (86 lines)
- `ui.py:1971` — `__init__()` (85 lines)
- `tools/eval_harness.py:125` — `run_tool_call_suite()` (85 lines)
- `memory/hybrid_memory.py:305` — `_retrieve()` (85 lines)
- `agent/executor.py:38` — `_run_generated_code()` (83 lines)
- `tests/test_action_launcher.py:196` — `test_chat_input_is_discoverable_and_does_not_drop_unsent_text()` (83 lines)
- `actions/osint_tools.py:952` — `github_user_osint()` (81 lines)
- `tests/test_listen_mode.py:163` — `_harness()` (81 lines)

## Stray `print()` calls
Found outside of scripts/tests — consider routing through `core/observability.py` or logging instead.
- `agent/executor.py:92`
- `agent/executor.py:145`
- `agent/executor.py:157`
- `agent/executor.py:160`
- `agent/executor.py:211`
- `agent/executor.py:298`
- `agent/executor.py:316`
- `agent/executor.py:345`
- `agent/executor.py:357`
- `agent/executor.py:367`
- `agent/executor.py:382`
- `agent/executor.py:408`
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
- `tests/test_trace_log.py:117`
- `tools/trace_view.py:104`
- `tools/trace_view.py:108`
- `tools/trace_view.py:115`
- `tools/trace_view.py:123`
- ...and 211 more
