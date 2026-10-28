"""core/listen_mode.py - which gate sits in front of speech-to-text (roadmap #95).

Why this exists
---------------
The microphone loop uses an energy VAD, so ANY loud audio (a TikTok playing in
the background, a TV, a video call) is treated as speech and transcribed. Only a
gate in front of the VAD fixes that. Three listen modes:

  always  Mic is always open (the old default). Background audio gets transcribed.
  wake    Mic is gated by the "Hey Jarvis" wake word (needs openwakeword + model).
  ptt     Mic is gated by a global push-to-talk hotkey: press, speak, done.

`auto` (the default when neither `listen_mode` nor the legacy `wake_word_enabled`
key is in the config) picks `wake` when the wake model is ready, else `ptt` when a
hotkey can be used, else `always` -- and says so in the log, so Aether never goes
deaf without telling you.

Config keys (config/api_keys.json):
    listen_mode       "auto" (default) | "always" | "wake" | "ptt"
    ptt_shortcut        default "ctrl+alt+space"
    ptt_idle_secs     default 6 -- how long the window stays open waiting for you to start speaking

An explicit legacy `wake_word_enabled` (true or false) is still honoured when
`listen_mode` is unset, so existing setups behave exactly as before.

Everything here is pure logic (no audio, no model), so it is unit-testable.
"""
from __future__ import annotations

import importlib.util
import time
from typing import Callable

MODES = ("always", "wake", "ptt")
DEFAULT_PTT_HOTKEY = "ctrl+alt+space"
DEFAULT_PTT_IDLE_S = 6.0

_WAKE_NOT_READY = "wake word enabled but openwakeword is not ready - staying always-awake"


def _truthy(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def keyboard_available() -> bool:
    """True if the optional `keyboard` package (global hotkeys) is importable."""
    try:
        return importlib.util.find_spec("keyboard") is not None
    except Exception:
        return False


def resolve_listen_mode(cfg: dict, *, wake_ready: bool, ptt_available: bool) -> tuple[str, str]:
    """Decide the listen mode. Returns (mode, note); `note` is "" or a log-worthy warning.

    Fallbacks never leave the user deaf: whenever a gate can't be used the result
    is `always`, with a note saying why.
    """
    cfg = cfg or {}
    note = ""
    raw = str(cfg.get("listen_mode", "") or "").strip().lower()
    if raw and raw not in MODES + ("auto",):
        note = f"unknown listen_mode '{raw}' - using auto"
        raw = ""
    if not raw:
        if "wake_word_enabled" in cfg:               # legacy explicit choice is respected
            raw = "wake" if _truthy(cfg["wake_word_enabled"]) else "always"
        else:
            raw = "auto"

    if raw == "always":
        return "always", note
    if raw == "wake":
        if wake_ready:
            return "wake", note
        return "always", _WAKE_NOT_READY
    if raw == "ptt":
        if ptt_available:
            return "ptt", note
        return "always", ("push-to-talk needs the 'keyboard' package (pip install keyboard) - "
                          "staying always-listening")
    # auto
    if wake_ready:
        return "wake", note
    if ptt_available:
        return "ptt", note
    return "always", ("no wake word model and no push-to-talk hotkey available - staying "
                      "always-listening, so loud background audio WILL be transcribed "
                      "(pip install keyboard to enable push-to-talk)")


class PushToTalkGate:
    """Awake/asleep state for push-to-talk. Same interface as core.wake_word.WakeGate
    so the mic loop can use either: enabled / awake / wake() / touch() /
    check_sleep() / utterance_done().

    Asleep until the hotkey is pressed (wake()). Then awake until the utterance is
    done (utterance_done()) or `idle_s` passes without any speech (check_sleep()).
    touch() restarts the idle window, so the loop calls it while the user is
    mid-sentence and the window can never close on them.
    """

    def __init__(self, idle_s: float = DEFAULT_PTT_IDLE_S, clock=time.monotonic):
        self._clock = clock
        self.idle_s = float(idle_s)
        self.enabled = True
        self._awake = False
        self._last = clock()

    @property
    def awake(self) -> bool:
        return (not self.enabled) or self._awake

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self._awake = False
        self._last = self._clock()

    def wake(self) -> bool:
        """Hotkey pressed. Opens (or refreshes) the window; True if it was closed before."""
        was_asleep = self.enabled and not self._awake
        if self.enabled:
            self._awake = True
        self._last = self._clock()
        return was_asleep

    def touch(self) -> None:
        self._last = self._clock()

    def utterance_done(self) -> None:
        """One utterance captured: close the window until the next press."""
        self._awake = False

    def check_sleep(self, speaking: bool = False) -> bool:
        """True if the window just closed because nobody spoke for `idle_s`."""
        if not self.enabled or not self._awake or speaking:
            return False
        if self._clock() - self._last > self.idle_s:
            self._awake = False
            return True
        return False


def register_push_to_talk(hotkey: str, on_press: Callable[[], None]
                          ) -> tuple[bool, Callable[[], None], str]:
    """Register a global hotkey. Returns (ok, unregister, why_not).

    Never raises: `keyboard` can be missing, or refuse to hook (Linux needs root,
    macOS needs Accessibility permission). The caller falls back to always-listening.
    """
    try:
        import keyboard
        handle = keyboard.add_hotkey(hotkey, on_press, suppress=False)
    except Exception as e:  # noqa: BLE001 - any failure means "not available"
        return False, (lambda: None), f"could not register hotkey '{hotkey}': {e}"

    def _unregister() -> None:
        try:
            keyboard.remove_hotkey(handle)
        except Exception:  # noqa: BLE001
            pass

    return True, _unregister, ""
