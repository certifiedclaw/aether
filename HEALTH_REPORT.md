# Aether — Health Report

> **Auto-generated.** Rerun `python -m tools.maintenance.health_check` (or `run_all`) to refresh. Fix things here, don't edit this file directly.

Generated: 2026-10-04T21:26:28.592185+00:00
Files scanned: 111

## Summary

| Check | Count |
|---|---|
| Files that fail to parse | 0 |
| Bare `except:` clauses | 0 |
| Files over 600 lines | 13 |
| Functions over 80 lines | 37 |
| Stray `print()` calls (outside scripts) | 183 |
| TODO / FIXME / XXX comments | 0 |
| Unreadable files | 0 |

## Files over 600 lines
- `ui.py` — 2639 lines
- `tests\test_all_critical.py` — 2009 lines
- `actions\osint_tools.py` — 1184 lines
- `main.py` — 1064 lines
- `actions\game_updater.py` — 1048 lines
- `actions\extra_tools.py` — 1005 lines
- `dashboard\server.py` — 965 lines
- `actions\browser_control.py` — 911 lines
- `core\llm_client.py` — 895 lines
- `actions\file_processor.py` — 834 lines
- `actions\computer_settings.py` — 673 lines
- `core\conversation_core.py` — 661 lines
- `actions\file_controller.py` — 618 lines

## Functions over 80 lines
- `core\conversation_core.py:270` — `_process_message_inner()` (291 lines)
- `actions\computer_control.py:353` — `computer_control()` (155 lines)
- `core\llm_client.py:743` — `call_llm_stream()` (152 lines)
- `core\llm_client.py:598` — `_stream_openai()` (142 lines)
- `ui.py:409` — `paintEvent()` (140 lines)
- `actions\extra_tools.py:865` — `run_extra_tool()` (140 lines)
- `ui.py:1299` — `_build_llm_section()` (139 lines)
- `actions\dev_agent.py:348` — `_build_project()` (139 lines)
- `main.py:924` — `main()` (137 lines)
- `actions\file_processor.py:582` — `_process_video()` (127 lines)
- `agent\executor.py:295` — `execute()` (123 lines)
- `main.py:795` — `run()` (122 lines)
- `dashboard\server.py:130` — `_ensure_network_access_windows()` (120 lines)
- `actions\game_updater.py:926` — `game_updater()` (115 lines)
- `core\llm_client.py:404` — `call_llm()` (111 lines)
- `actions\browser_control.py:421` — `_launch()` (104 lines)
- `tests\test_all_critical.py:1046` — `test_confirm_blocking_dialog_thread_safety()` (102 lines)
- `actions\reminder.py:41` — `_write_notify_script()` (98 lines)
- `dashboard\server.py:540` — `_register_auth_routes()` (98 lines)
- `actions\file_processor.py:313` — `_process_data()` (95 lines)
- `core\smart_context.py:55` — `compress_if_needed()` (93 lines)
- `ui.py:1440` — `_build_tts_section()` (92 lines)
- `core\diagnostics.py:22` — `run_all()` (91 lines)
- `actions\file_processor.py:81` — `_process_image()` (90 lines)
- `ui.py:709` — `__init__()` (88 lines)
- `actions\browser_control.py:816` — `browser_control()` (88 lines)
- `actions\osint_tools.py:887` — `extract_metadata()` (88 lines)
- `core\chat_commands.py:38` — `_handle_chat_command()` (88 lines)
- `actions\code_helper.py:370` — `_screen_debug_action()` (87 lines)
- `main.py:972` — `runner()` (86 lines)
- `ui.py:1759` — `__init__()` (85 lines)
- `memory\hybrid_memory.py:291` — `_retrieve()` (85 lines)
- `ui.py:2235` — `_build_right_panel()` (84 lines)
- `actions\computer_settings.py:590` — `computer_settings()` (83 lines)
- `ui.py:1883` — `__init__()` (81 lines)
- `actions\desktop.py:130` — `set_wallpaper()` (81 lines)
- `actions\osint_tools.py:781` — `github_user_osint()` (81 lines)

## Stray `print()` calls
Found outside of scripts/tests — consider routing through `core/observability.py` or logging instead.
- `config_validator.py:197`
- `agent\error_handler.py:58`
- `agent\error_handler.py:96`
- `agent\error_handler.py:100`
- `agent\error_handler.py:141`
- `agent\executor.py:77`
- `agent\executor.py:130`
- `agent\executor.py:142`
- `agent\executor.py:145`
- `agent\executor.py:196`
- `agent\executor.py:283`
- `agent\executor.py:301`
- `agent\executor.py:330`
- `agent\executor.py:342`
- `agent\executor.py:348`
- `agent\executor.py:363`
- `agent\executor.py:389`
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
- `core\listen_loops.py:131`
- `core\listen_loops.py:166`
- `core\llm_client.py:157`
- `core\llm_client.py:159`
- `core\llm_client.py:162`
- `core\llm_client.py:180`
- `core\llm_client.py:187`
- `core\llm_client.py:190`
- `core\llm_client.py:197`
- `core\llm_client.py:200`
- `core\llm_client.py:221`
- `core\llm_client.py:241`
- `core\llm_client.py:244`
- ...and 133 more
