# Aether — Health Report

> **Auto-generated.** Rerun `python -m tools.maintenance.health_check` (or `run_all`) to refresh. Fix things here, don't edit this file directly.

Generated: 2026-07-13T18:07:17.253408+00:00
Files scanned: 75

## Summary

| Check | Count |
|---|---|
| Files that fail to parse | 0 |
| Bare `except:` clauses | 0 |
| Files over 600 lines | 10 |
| Functions over 80 lines | 32 |
| Stray `print()` calls (outside scripts) | 145 |
| TODO / FIXME / XXX comments | 1 |
| Unreadable files | 0 |

## Files over 600 lines
- `ui.py` — 2392 lines
- `main.py` — 2297 lines
- `actions\osint_tools.py` — 1180 lines
- `actions\game_updater.py` — 1048 lines
- `actions\extra_tools.py` — 1005 lines
- `actions\browser_control.py` — 893 lines
- `dashboard\server.py` — 853 lines
- `actions\file_processor.py` — 834 lines
- `core\llm_client.py` — 703 lines
- `actions\computer_settings.py` — 673 lines

## Functions over 80 lines
- `ui.py:1085` — `__init__()` (333 lines)
- `main.py:1457` — `_process_message_inner()` (328 lines)
- `dashboard\server.py:450` — `_build_app()` (306 lines)
- `main.py:1141` — `_execute_tool()` (294 lines)
- `dashboard\server.py:96` — `_ensure_network_access()` (208 lines)
- `main.py:1934` — `_init_new_subsystems()` (158 lines)
- `actions\computer_control.py:353` — `computer_control()` (155 lines)
- `ui.py:409` — `paintEvent()` (140 lines)
- `actions\extra_tools.py:865` — `run_extra_tool()` (140 lines)
- `actions\dev_agent.py:348` — `_build_project()` (139 lines)
- `core\llm_client.py:574` — `call_llm_stream()` (129 lines)
- `actions\file_processor.py:582` — `_process_video()` (127 lines)
- `core\llm_client.py:445` — `_stream_openai()` (126 lines)
- `agent\executor.py:295` — `execute()` (119 lines)
- `actions\game_updater.py:926` — `game_updater()` (115 lines)
- `core\llm_client.py:273` — `call_llm()` (111 lines)
- `actions\browser_control.py:409` — `_launch()` (104 lines)
- `main.py:2098` — `run()` (101 lines)
- `actions\reminder.py:41` — `_write_notify_script()` (98 lines)
- `actions\file_processor.py:313` — `_process_data()` (95 lines)
- `core\smart_context.py:34` — `compress_if_needed()` (93 lines)
- `actions\file_processor.py:81` — `_process_image()` (90 lines)
- `main.py:2206` — `main()` (88 lines)
- `ui.py:709` — `__init__()` (88 lines)
- `actions\osint_tools.py:883` — `extract_metadata()` (88 lines)
- `actions\code_helper.py:370` — `_screen_debug_action()` (87 lines)
- `ui.py:1569` — `__init__()` (85 lines)
- `ui.py:2029` — `_build_right_panel()` (84 lines)
- `actions\computer_settings.py:590` — `computer_settings()` (83 lines)
- `actions\browser_control.py:804` — `browser_control()` (82 lines)
- `actions\desktop.py:130` — `set_wallpaper()` (81 lines)
- `actions\osint_tools.py:777` — `github_user_osint()` (81 lines)

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
- `agent\planner.py:84`
- `agent\planner.py:252`
- `agent\planner.py:256`
- `agent\planner.py:258`
- `agent\planner.py:262`
- `agent\planner.py:265`
- `agent\planner.py:270`
- `agent\planner.py:309`
- `agent\planner.py:312`
- `agent\task_queue.py:65`
- `agent\task_queue.py:71`
- `agent\task_queue.py:97`
- `agent\task_queue.py:111`
- `agent\task_queue.py:175`
- `agent\task_queue.py:196`
- `agent\task_queue.py:198`
- `agent\task_queue.py:205`
- `core\diagnostics.py:113`
- `core\llm_client.py:120`
- `core\llm_client.py:122`
- `core\llm_client.py:125`
- `core\llm_client.py:143`
- `core\llm_client.py:150`
- `core\llm_client.py:153`
- `core\llm_client.py:160`
- `core\llm_client.py:163`
- `core\llm_client.py:184`
- `core\llm_client.py:203`
- `core\llm_client.py:206`
- `core\llm_client.py:223`
- `core\llm_client.py:226`
- `core\llm_client.py:312`
- `core\llm_client.py:334`
- ...and 95 more

## TODO / FIXME / XXX
- `[TODO]` `tools\maintenance\health_check.py:157` — / FIXME / XXX")
