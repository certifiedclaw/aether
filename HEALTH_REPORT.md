# Aether — Health Report

> **Auto-generated.** Rerun `python -m tools.maintenance.health_check` (or `run_all`) to refresh. Fix things here, don't edit this file directly.

Generated: 2026-10-17T22:55:52.774340+00:00
Files scanned: 186

## Summary

| Check | Count |
|---|---|
| Files that fail to parse | 0 |
| Bare `except:` clauses | 0 |
| Files over 600 lines | 15 |
| Functions over 80 lines | 55 |
| Stray `print()` calls (outside scripts) | 238 |
| TODO / FIXME / XXX comments | 0 |
| Unreadable files | 0 |

## Files over 600 lines
- `ui.py` — 3449 lines
- `tests\test_all_critical.py` — 2152 lines
- `core\llm_client.py` — 1448 lines
- `actions\osint_tools.py` — 1355 lines
- `dashboard\server.py` — 1193 lines
- `main.py` — 1169 lines
- `core\conversation_core.py` — 1083 lines
- `actions\extra_tools.py` — 1076 lines
- `actions\game_updater.py` — 1050 lines
- `actions\file_processor.py` — 950 lines
- `actions\browser_control.py` — 913 lines
- `actions\computer_settings.py` — 734 lines
- `tests\test_invariants.py` — 674 lines
- `actions\desktop.py` — 656 lines
- `actions\file_controller.py` — 647 lines

## Functions over 80 lines
- `core\conversation_core.py:459` — `_process_message_inner()` (506 lines)
- `core\chat_commands.py:47` — `_handle_chat_command()` (247 lines)
- `ui.py:2586` — `_open_action_catalog()` (205 lines)
- `ui.py:2317` — `_build_right_panel()` (196 lines)
- `core\llm_client.py:1258` — `call_llm_stream()` (190 lines)
- `core\llm_client.py:889` — `_stream_openai_once()` (186 lines)
- `ui.py:1315` — `_build_llm_section()` (167 lines)
- `actions\computer_control.py:355` — `computer_control()` (155 lines)
- `actions\dev_agent.py:373` — `_build_project()` (154 lines)
- `ui.py:2827` — `_open_action_form()` (149 lines)
- `ui.py:412` — `paintEvent()` (140 lines)
- `actions\extra_tools.py:936` — `run_extra_tool()` (140 lines)
- `main.py:1029` — `main()` (137 lines)
- `main.py:889` — `run()` (133 lines)
- `dashboard\server.py:660` — `_register_auth_routes()` (130 lines)
- `actions\file_processor.py:582` — `_process_video()` (127 lines)
- `actions\osint_tools.py:564` — `tech_stack()` (127 lines)
- `agent\executor.py:310` — `execute()` (127 lines)
- `dashboard\server.py:190` — `_ensure_network_access_windows()` (120 lines)
- `tests\test_action_launcher.py:10` — `test_action_launcher_builds_form_and_dispatches_values()` (117 lines)
- `actions\game_updater.py:928` — `game_updater()` (115 lines)
- `tests\test_all_critical.py:760` — `test_setup_overlay_refactor_preserves_behavior()` (115 lines)
- `core\llm_client.py:615` — `call_llm()` (112 lines)
- `core\listen_loops.py:84` — `_listen_whisper()` (111 lines)
- `actions\browser_control.py:421` — `_launch()` (104 lines)
- `dashboard\server.py:958` — `_register_file_routes()` (104 lines)
- `tests\test_all_critical.py:1178` — `test_confirm_blocking_dialog_thread_safety()` (102 lines)
- `core\portable_bundle.py:63` — `_write_bundle()` (101 lines)
- `actions\reminder.py:41` — `_write_notify_script()` (98 lines)
- `actions\file_processor.py:711` — `_process_archive()` (97 lines)
- `core\smart_context.py:55` — `compress_if_needed()` (96 lines)
- `actions\file_processor.py:313` — `_process_data()` (95 lines)
- `core\echo.py:197` — `is_user_speech()` (94 lines)
- `core\llm_client.py:1325` — `_do_stream()` (93 lines)
- `ui.py:1484` — `_build_tts_section()` (92 lines)
- `dashboard\openai_api.py:94` — `build_router()` (92 lines)
- `actions\code_helper.py:413` — `_screen_debug_action()` (91 lines)
- `core\conversation_core.py:230` — `_prepare_conversation_messages()` (91 lines)
- `core\diagnostics.py:22` — `run_all()` (91 lines)
- `ui.py:1956` — `__init__()` (90 lines)
- `actions\file_processor.py:81` — `_process_image()` (90 lines)
- `actions\desktop.py:302` — `set_wallpaper()` (89 lines)
- `ui.py:723` — `__init__()` (88 lines)
- `actions\browser_control.py:818` — `browser_control()` (88 lines)
- `actions\osint_tools.py:1058` — `extract_metadata()` (88 lines)
- `actions\computer_settings.py:647` — `computer_settings()` (87 lines)
- `actions\desktop.py:569` — `desktop_control()` (87 lines)
- `main.py:1077` — `runner()` (86 lines)
- `tools\live_grader.py:132` — `grade_overall()` (86 lines)
- `ui.py:1830` — `__init__()` (85 lines)
- `memory\hybrid_memory.py:291` — `_retrieve()` (85 lines)
- `tools\eval_harness.py:125` — `run_tool_call_suite()` (85 lines)
- `agent\executor.py:38` — `_run_generated_code()` (83 lines)
- `tests\test_action_launcher.py:155` — `test_chat_input_is_discoverable_and_does_not_drop_unsent_text()` (83 lines)
- `actions\osint_tools.py:952` — `github_user_osint()` (81 lines)

## Stray `print()` calls
Found outside of scripts/tests — consider routing through `core/observability.py` or logging instead.
- `config_validator.py:197`
- `agent\error_handler.py:58`
- `agent\error_handler.py:96`
- `agent\error_handler.py:100`
- `agent\error_handler.py:141`
- `agent\executor.py:92`
- `agent\executor.py:145`
- `agent\executor.py:157`
- `agent\executor.py:160`
- `agent\executor.py:211`
- `agent\executor.py:298`
- `agent\executor.py:316`
- `agent\executor.py:345`
- `agent\executor.py:357`
- `agent\executor.py:367`
- `agent\executor.py:382`
- `agent\executor.py:408`
- `agent\planner.py:82`
- `agent\planner.py:241`
- `agent\planner.py:245`
- `agent\planner.py:247`
- `agent\planner.py:251`
- `agent\planner.py:254`
- `agent\planner.py:259`
- `agent\planner.py:300`
- `agent\planner.py:303`
- `agent\task_queue.py:67`
- `agent\task_queue.py:73`
- `agent\task_queue.py:99`
- `agent\task_queue.py:122`
- `agent\task_queue.py:203`
- `agent\task_queue.py:225`
- `agent\task_queue.py:227`
- `agent\task_queue.py:235`
- `core\config_validator.py:197`
- `core\diagnostics.py:134`
- `core\doctor.py:383`
- `core\doctor.py:385`
- `core\headless_ui.py:70`
- `core\listen_loops.py:194`
- `core\listen_loops.py:229`
- `core\llm_client.py:299`
- `core\llm_client.py:301`
- `core\llm_client.py:304`
- `core\llm_client.py:322`
- `core\llm_client.py:329`
- `core\llm_client.py:332`
- `core\llm_client.py:339`
- `core\llm_client.py:342`
- `core\llm_client.py:363`
- ...and 188 more
