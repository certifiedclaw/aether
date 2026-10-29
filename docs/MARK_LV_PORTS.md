# Ported from MARK LV

Source: MARK LV by FatihMakes (CC BY-NC 4.0, non-commercial use only; attribution headers are kept in the ported files).
All three features are config switches in `config/api_keys.json`; defaults keep Aether fully local.

| Key | Default | Effect |
|---|---|---|
| `echo_guard` | `true` | Content-based echo guard (`core/echo.py`). Drops Aether's own voice tail after a reply, and replaces the old loudness-only barge-in (which interrupted itself on speakers). `false` restores the old RMS behavior. |
| `wake_word_enabled` | `true` (was `false` before roadmap #95) | "Hey Jarvis" gate (`core/wake_word.py`, needs `pip install openwakeword` + one-time model download). Sleeps after 2 min of silence. If the model is missing, Aether logs a warning and stays always-awake. |
| `gemini_for_tools` | `false` | Opt-in cloud route. When `true` AND `gemini_api_key` is set AND the primary provider is local, tool-using turns go to Gemini. This sends the message, injected memory and tool results to Google; each such turn is logged as `SYS: tool turn routed to Gemini`. Chit-chat (QUICK tier) never goes. |

Not yet ported: push-to-talk (`core/hotkey.py`), audio device picker (`core/audio_devices.py`), self-describing skill loader (`core/action_loader.py`), avatar/lip-sync.
