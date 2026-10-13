# Aether — Health Report

> **Auto-generated.** Rerun `python -m tools.maintenance.health_check` (or `run_all`) to refresh. Fix things here, don't edit this file directly.

Generated: 2026-10-13T02:12:53.449280+00:00
Files scanned: 159

## Summary

| Check | Count |
|---|---|
| Files that fail to parse | 0 |
| Bare `except:` clauses | 0 |
| Files over 600 lines | 15 |
| Functions over 80 lines | 45 |
| Stray `print()` calls (outside scripts) | 225 |
| TODO / FIXME / XXX comments | 0 |
| Unreadable files | 0 |

## Files over 600 lines
- `ui.py` — 2751 lines
- `tests\test_all_critical.py` — 2150 lines
- `core\llm_client.py` — 1331 lines
- `actions\osint_tools.py` — 1200 lines
- `main.py` — 1122 lines
- `actions\extra_tools.py` — 1071 lines
- `dashboard\server.py` — 1061 lines
- `actions\game_updater.py` — 1050 lines
- `core\conversation_core.py` — 967 lines
- `actions\file_processor.py` — 942 lines
- `actions\browser_control.py` — 913 lines
- `actions\computer_settings.py` — 734 lines
- `tests\test_invariants.py` — 674 lines
- `actions\desktop.py` — 656 lines
- `actions\file_controller.py` — 647 lines

## Functions over 80 lines
- `core\conversation_core.py:418` — `_process_message_inner()` (440 lines)
- `core\chat_commands.py:47` — `_handle_chat_command()` (193 lines)
- `core\llm_client.py:791` — `_stream_openai_once()` (181 lines)
- `core\llm_client.py:1155` — `call_llm_stream()` (176 lines)
- `ui.py:1284` — `_build_llm_section()` (168 lines)
- `actions\computer_control.py:355` — `computer_control()` (155 lines)
- `actions\dev_agent.py:349` — `_build_project()` (154 lines)
- `ui.py:411` — `paintEvent()` (140 lines)
- `actions\extra_tools.py:931` — `run_extra_tool()` (140 lines)
- `main.py:982` — `main()` (137 lines)
- `actions\file_processor.py:582` — `_process_video()` (127 lines)
- `agent\executor.py:310` — `execute()` (127 lines)
- `dashboard\server.py:188` — `_ensure_network_access_windows()` (120 lines)
- `main.py:860` — `run()` (115 lines)
- `actions\game_updater.py:928` — `game_updater()` (115 lines)
- `dashboard\server.py:617` — `_register_auth_routes()` (115 lines)
- `tests\test_all_critical.py:760` — `test_setup_overlay_refactor_preserves_behavior()` (113 lines)
- `core\llm_client.py:517` — `call_llm()` (112 lines)
- `core\listen_loops.py:84` — `_listen_whisper()` (111 lines)
- `actions\browser_control.py:421` — `_launch()` (104 lines)
- `tests\test_all_critical.py:1176` — `test_confirm_blocking_dialog_thread_safety()` (102 lines)
- `actions\reminder.py:41` — `_write_notify_script()` (98 lines)
- `core\smart_context.py:55` — `compress_if_needed()` (96 lines)
- `actions\file_processor.py:313` — `_process_data()` (95 lines)
- `ui.py:2278` — `_build_right_panel()` (94 lines)
- `core\echo.py:197` — `is_user_speech()` (94 lines)
- `ui.py:1454` — `_build_tts_section()` (92 lines)
- `actions\code_helper.py:413` — `_screen_debug_action()` (91 lines)
- `core\diagnostics.py:22` — `run_all()` (91 lines)
- `actions\file_processor.py:81` — `_process_image()` (90 lines)
- `actions\desktop.py:302` — `set_wallpaper()` (89 lines)
- `actions\file_processor.py:711` — `_process_archive()` (89 lines)
- `ui.py:692` — `__init__()` (88 lines)
- `actions\browser_control.py:818` — `browser_control()` (88 lines)
- `actions\osint_tools.py:903` — `extract_metadata()` (88 lines)
- `core\llm_client.py:1222` — `_do_stream()` (88 lines)
- `actions\computer_settings.py:647` — `computer_settings()` (87 lines)
- `actions\desktop.py:569` — `desktop_control()` (87 lines)
- `main.py:1030` — `runner()` (86 lines)
- `ui.py:1921` — `__init__()` (86 lines)
- `ui.py:1795` — `__init__()` (85 lines)
- `core\conversation_core.py:202` — `_prepare_conversation_messages()` (85 lines)
- `memory\hybrid_memory.py:291` — `_retrieve()` (85 lines)
- `agent\executor.py:38` — `_run_generated_code()` (83 lines)
- `actions\osint_tools.py:797` — `github_user_osint()` (81 lines)

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
- `core\headless_ui.py:70`
- `core\listen_loops.py:194`
- `core\listen_loops.py:229`
- `core\llm_client.py:268`
- `core\llm_client.py:270`
- `core\llm_client.py:273`
- `core\llm_client.py:291`
- `core\llm_client.py:298`
- `core\llm_client.py:301`
- `core\llm_client.py:308`
- `core\llm_client.py:311`
- `core\llm_client.py:332`
- `core\llm_client.py:353`
- `core\llm_client.py:356`
- ...and 175 more
