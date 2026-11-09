# Aether — Health Report

> **Auto-generated.** Rerun `python -m tools.maintenance.health_check` (or `run_all`) to refresh. Fix things here, don't edit this file directly.

Generated: 2026-11-09T01:06:08.301884+00:00
Files scanned: 269

## Summary

| Check | Count |
|---|---|
| Files that fail to parse | 0 |
| Bare `except:` clauses | 0 |
| Files over 600 lines | 19 |
| Functions over 80 lines | 67 |
| Stray `print()` calls (outside scripts) | 0 |
| TODO / FIXME / XXX comments | 0 |
| Unreadable files | 0 |

## Files over 600 lines
- `ui.py` — 4149 lines
- `tests/test_all_critical.py` — 2160 lines
- `core/conversation_core.py` — 1741 lines
- `core/llm_client.py` — 1589 lines
- `actions/osint_tools.py` — 1441 lines
- `main.py` — 1292 lines
- `dashboard/server.py` — 1169 lines
- `core/trace_log.py` — 1107 lines
- `actions/extra_tools.py` — 1077 lines
- `actions/game_updater.py` — 1052 lines
- `actions/file_processor.py` — 1036 lines
- `actions/browser_control.py` — 941 lines
- `desktop_mate.py` — 835 lines
- `actions/computer_settings.py` — 755 lines
- `core/updater.py` — 738 lines
- `tests/test_invariants.py` — 674 lines
- `core/tool_declarations.py` — 656 lines
- `actions/desktop.py` — 655 lines
- `actions/file_controller.py` — 653 lines

## Functions over 80 lines
- `core/conversation_core.py:735` — `_process_message_inner()` (700 lines)
- `core/chat_commands.py:70` — `_handle_chat_command()` (306 lines)
- `ui.py:2848` — `_build_right_panel()` (285 lines)
- `ui.py:3206` — `_open_action_catalog()` (205 lines)
- `core/llm_client.py:1397` — `_call_llm_stream_impl()` (192 lines)
- `core/llm_client.py:975` — `_stream_openai_once()` (188 lines)
- `ui.py:1645` — `_build_llm_section()` (180 lines)
- `core/listen_loops.py:142` — `_listen_whisper()` (158 lines)
- `actions/dev_agent.py:402` — `_build_project()` (154 lines)
- `actions/computer_control.py:354` — `computer_control()` (154 lines)
- `main.py:984` — `run()` (153 lines)
- `ui.py:3447` — `_open_action_form()` (149 lines)
- `main.py:1144` — `main()` (145 lines)
- `ui.py:498` — `paintEvent()` (140 lines)
- `actions/extra_tools.py:937` — `run_extra_tool()` (140 lines)
- `agent/executor.py:357` — `execute()` (139 lines)
- `tests/test_action_launcher.py:12` — `test_action_launcher_builds_form_and_dispatches_values()` (138 lines)
- `core/conversation_core.py:521` — `_execute_tool_calls()` (135 lines)
- `core/conversation_core.py:385` — `_prepare_conversation_messages()` (134 lines)
- `dashboard/server.py:508` — `_register_auth_routes()` (131 lines)
- `actions/file_processor.py:668` — `_process_video()` (127 lines)
- `actions/osint_tools.py:565` — `tech_stack()` (127 lines)
- `actions/deep_research.py:329` — `deep_research()` (117 lines)
- `ui.py:1339` — `__init__()` (116 lines)
- `actions/game_updater.py:930` — `game_updater()` (115 lines)
- `tests/test_all_critical.py:764` — `test_setup_overlay_refactor_preserves_behavior()` (115 lines)
- `core/llm_client.py:672` — `_call_llm_impl()` (112 lines)
- `dashboard/firewall.py:56` — `_ensure_network_access_windows()` (108 lines)
- `actions/browser_control.py:449` — `_launch()` (104 lines)
- `dashboard/server.py:869` — `_register_file_routes()` (104 lines)
- `tests/test_all_critical.py:1191` — `test_confirm_blocking_dialog_thread_safety()` (102 lines)
- `core/portable_bundle.py:65` — `_write_bundle()` (101 lines)
- `tests/test_action_launcher.py:227` — `test_chat_input_is_discoverable_and_does_not_drop_unsent_text()` (100 lines)
- `actions/reminder.py:40` — `_write_notify_script()` (98 lines)
- `actions/threat_intel.py:54` — `threat_intel_brief()` (97 lines)
- `actions/file_processor.py:797` — `_process_archive()` (97 lines)
- `core/smart_context.py:55` — `compress_if_needed()` (96 lines)
- `actions/file_processor.py:162` — `_process_image()` (95 lines)
- `actions/file_processor.py:399` — `_process_data()` (95 lines)
- `core/llm_client.py:1464` — `_do_stream()` (95 lines)
- `agent/executor.py:51` — `_run_generated_code()` (94 lines)
- `core/echo.py:197` — `is_user_speech()` (94 lines)
- `ui.py:2484` — `__init__()` (93 lines)
- `desktop_mate.py:578` — `_draw_eyes()` (93 lines)
- `ui.py:1827` — `_build_tts_section()` (92 lines)
- `desktop_mate.py:699` — `_draw_extras()` (92 lines)
- `actions/code_helper.py:431` — `_screen_debug_action()` (92 lines)
- `dashboard/openai_api.py:94` — `build_router()` (92 lines)
- `core/prompt_builder.py:212` — `_build_system_prompt()` (92 lines)
- `core/diagnostics.py:22` — `run_all()` (91 lines)
- `actions/desktop.py:302` — `set_wallpaper()` (89 lines)
- `actions/osint_tools.py:1139` — `extract_metadata()` (88 lines)
- `memory/memory_manager.py:130` — `format_memory_for_prompt()` (88 lines)
- `main.py:1199` — `runner()` (87 lines)
- `actions/browser_control.py:847` — `browser_control()` (87 lines)
- `integrations/langchain_tools.py:253` — `lc_research_agent()` (87 lines)
- `actions/computer_settings.py:669` — `computer_settings()` (86 lines)
- `actions/desktop.py:569` — `desktop_control()` (86 lines)
- `tools/live_grader.py:132` — `grade_overall()` (86 lines)
- `memory/hybrid_memory.py:318` — `_retrieve()` (86 lines)
- `ui.py:2358` — `__init__()` (85 lines)
- `tools/eval_harness.py:125` — `run_tool_call_suite()` (85 lines)
- `core/updater.py:510` — `_run_tests()` (84 lines)
- `core/memory_tools.py:143` — `import_memory()` (82 lines)
- `actions/osint_tools.py:1033` — `github_user_osint()` (81 lines)
- `agent/executor.py:245` — `_call_tool()` (81 lines)
- `tests/test_listen_mode.py:163` — `_harness()` (81 lines)
