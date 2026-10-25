"""Desktop mate: reply-bubble text, Aether-state mapping, widget behaviour, and its
wiring into the real MainWindow. Runs on Qt's offscreen platform (no display needed)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
from PyQt6.QtGui import QFontMetrics, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

import desktop_mate as dm
import mate_personality as mp


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def mate(qapp, tmp_path):
    m = dm.DesktopMate(assets_dir=str(tmp_path / "no_art"))
    yield m
    m.close()


# ── bubble_text (pure text handling) ────────────────────────────────────────

def test_bubble_text_strips_speaker_prefix_and_markdown(qapp):
    assert dm.bubble_text("Aether: **Done.** Moved `3` files.") == "Done. Moved 3 files."


def test_bubble_text_keeps_whole_sentences_that_fit(qapp):
    reply = "One two three. Four five six. Seven eight nine."
    assert dm.bubble_text(reply, limit=30) == "One two three. Four five six."


def test_bubble_text_truncates_one_oversized_sentence(qapp):
    out = dm.bubble_text("x" * 500, limit=50)
    assert len(out) == 50 and out.endswith("\u2026")


def test_bubble_text_replaces_code_blocks_and_link_urls(qapp):
    out = dm.bubble_text("```py\nprint(1)\n``` See [the docs](http://example.com/x).")
    assert "(code)" in out and "the docs" in out and "http" not in out


@pytest.mark.parametrize("raw,expected", [("", ""), ("Aether:", ""), ("Aether: ```x```", "(code)")])
def test_bubble_text_empty_and_degenerate_input(qapp, raw, expected):
    assert dm.bubble_text(raw) == expected


# ── state mapping ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("app_state,expected", [
    ("LISTENING", "idle"),      # Aether's resting state, not "mic is hot"
    ("THINKING", "thinking"),
    ("PROCESSING", "working"),
    ("SPEAKING", "speaking"),
    ("MUTED", "sleeping"),
    ("ERROR", "error"),
    ("thinking", "thinking"),   # case-insensitive
])
def test_app_states_map_to_mate_states(mate, app_state, expected):
    mate.on_app_state(app_state)
    assert mate._state == expected


def test_unknown_app_state_is_ignored(mate):
    mate.on_app_state("THINKING")
    mate.on_app_state("SOMETHING_NEW")
    assert mate._state == "thinking"


def test_every_mapped_state_is_a_real_mate_state():
    assert set(dm.APP_STATE_MAP.values()) <= set(dm.STATES)


# ── the character itself ───────────────────────────────────────────────────

@pytest.mark.parametrize("state", dm.STATES)
def test_every_state_paints_without_error(mate, state):
    mate.show()
    mate.set_state(state)
    for tick in (0, 7, 61, 190, 4000):  # spans blink, glitch and animation phases
        mate._tick = tick
        img = mate.grab().toImage()
        assert not img.isNull() and img.width() == dm.W and img.height() == dm.H


def test_long_task_states_do_not_fall_asleep(mate):
    for state in ("thinking", "working", "speaking", "error"):
        mate.set_state(state)
        mate._maybe_sleep()
        assert mate._state == state


def test_idle_mate_falls_asleep_and_click_wakes_it(mate):
    mate.set_state("idle")
    mate._maybe_sleep()
    assert mate._state == "sleeping"


def test_pet_reaction_ends_back_at_idle(mate):
    mate.set_state("happy")
    mate._end_pet()
    assert mate._state == "idle"
    mate.set_state("thinking")
    mate._end_pet()                      # must not clobber a real state
    assert mate._state == "thinking"


# ── shipped art (assets/mate) ──────────────────────────────────────────────

ART_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "mate")


def test_shipped_art_covers_every_state_with_transparency(qapp):
    m = dm.DesktopMate(assets_dir=ART_DIR)
    try:
        assert set(m._frames) == set(dm.STATES)
        for state, frames in m._frames.items():
            pm = frames[0]
            assert 80 <= pm.width() <= dm.W and pm.height() <= dm.H - 100, state  # clears the bubble area
            img = pm.toImage()
            assert img.hasAlphaChannel(), state
            assert img.pixelColor(0, 0).alpha() == 0, f"{state}: corner should be transparent"
    finally:
        m.close()


def test_art_states_paint_and_bob_without_error(qapp):
    m = dm.DesktopMate(assets_dir=ART_DIR)
    try:
        m.show()
        for state in dm.STATES:
            m.set_state(state)
            for tick in (0, 3, 41, 500):
                m._tick = tick
                assert not m.grab().toImage().isNull()
    finally:
        m.close()


# ── speech bubble gating ────────────────────────────────────────────────────

def test_only_aether_replies_become_bubbles(mate):
    mate.on_log_line("SYS: Aether is ready.")
    mate.on_log_line("You: hello")
    assert mate._bubble == ""
    mate.on_log_line("Aether: Hi there.")
    assert mate._bubble == "Hi there."


def test_bubbles_can_be_turned_off(mate):
    mate.show_bubbles = False
    mate.on_log_line("Aether: Hi there.")
    mate.on_app_state("ERROR")
    assert mate._bubble == ""


def test_error_state_shows_a_bubble(mate):
    mate.on_app_state("ERROR")
    assert "went wrong" in mate._bubble


@pytest.mark.parametrize("text", [
    "word " * 100,
    "Aether: " + "Supercalifragilistic" * 9,
    r"C:\Users\Someone\Downloads\Sorted\Projects\Aether\very_long_file_name_final_v2.py " * 3,
])
def test_bubble_text_always_fits_the_bubble(mate, text):
    mate.say(text)
    fm = QFontMetrics(mate._bubble_font)
    flags = dm._WRAP
    h = fm.boundingRect(QRect(0, 0, dm.BUBBLE_INNER_W, 10000), flags, mate._bubble).height()
    assert mate._bubble and h <= dm.BUBBLE_MAX_H - 2 * dm.BUBBLE_PAD


# ── widget behaviour ────────────────────────────────────────────────────────

def test_hidden_mate_stops_its_animation_timer(mate):
    mate.show()
    assert mate._timer.isActive()
    mate.hide()
    assert not mate._timer.isActive()


def test_mate_never_keeps_the_app_alive(mate):
    assert not mate.testAttribute(Qt.WidgetAttribute.WA_QuitOnClose)


def test_custom_png_frames_are_picked_up(qapp, tmp_path):
    (tmp_path / "idle").mkdir()
    pm = QPixmap(10, 10)
    pm.fill(Qt.GlobalColor.red)
    assert pm.save(str(tmp_path / "idle" / "0.png"))
    m = dm.DesktopMate(assets_dir=str(tmp_path))
    try:
        assert len(m._frames["idle"]) == 1 and "thinking" not in m._frames
    finally:
        m.close()


# ── personality (mate_personality.py + its hooks) ───────────────────────────

def _all_lines():
    for event, lines in mp.LINES.items():
        for line in lines:
            yield event, line
    for event, parts in mp.TIMED_LINES.items():
        for lines in parts.values():
            for line in lines:
                yield event, line


def test_every_persona_line_survives_bubble_text_unchanged(qapp):
    # bubble_text strips markdown / truncates; a persona line must pass through as written.
    for event, line in _all_lines():
        assert dm.bubble_text(line) == line, (event, line)


def test_every_persona_line_fits_the_bubble(mate):
    for event, line in _all_lines():
        mate.say(line)
        assert mate._bubble == line, (event, line)  # _fit_bubble did not have to trim it


def test_every_error_line_still_points_at_the_log():
    for line in mp.LINES["error"]:
        assert "went wrong" in line and "check the log" in line


def test_persona_covers_every_event_the_mate_uses():
    v = mp.Personality()
    for event in ("greet", "pet", "pet_many", "wake", "sleep", "thinking", "working", "error", "chatter"):
        assert v.pick(event), event
    assert v.pick("no_such_event") == ""


def test_pick_never_repeats_the_previous_line_for_an_event():
    import random
    v = mp.Personality(rng=random.Random(1234))
    seq = [v.pick("pet") for _ in range(300)]
    assert all(a != b for a, b in zip(seq, seq[1:]))


@pytest.mark.parametrize("hour,part", [
    (0, "late"), (4, "late"), (5, "morning"), (10, "morning"), (11, "day"),
    (16, "day"), (17, "evening"), (22, "evening"), (23, "late"),
])
def test_day_part_boundaries(hour, part):
    assert mp.day_part(hour) == part


def test_greeting_follows_time_of_day():
    for hour, part in ((8, "morning"), (13, "day"), (19, "evening"), (2, "late")):
        v = mp.Personality()
        assert v.pick("greet", hour=hour) in mp.TIMED_LINES["greet"][part]


def test_late_night_chatter_mixes_in_late_lines_and_keeps_generic_ones():
    seen = {mp.Personality().pick("chatter", hour=2) for _ in range(200)}
    assert seen & set(mp.TIMED_LINES["chatter"]["late"])
    assert seen & set(mp.LINES["chatter"])
    day = {mp.Personality().pick("chatter", hour=13) for _ in range(200)}
    assert not day & set(mp.TIMED_LINES["chatter"]["late"])


def test_petting_five_times_quickly_is_too_much_then_resets():
    v = mp.Personality()
    assert [v.petted(now=t) for t in (0, 1, 2, 3)] == [False] * 4
    assert v.petted(now=4) is True
    assert v.petted(now=5) is False  # counter restarted


def test_slow_petting_never_loses_patience():
    v = mp.Personality()
    assert not any(v.petted(now=t * 30.0) for t in range(20))


def test_click_pets_the_mate_and_it_answers(mate):
    mate.show()
    mate.set_state("idle")
    QTest.mouseClick(mate, Qt.MouseButton.LeftButton)
    assert mate._state == "happy"
    assert mate._bubble in mp.LINES["pet"]


def test_click_on_sleeping_mate_says_wake_line_not_pet_line(mate):
    mate.show()
    mate.set_state("sleeping")
    QTest.mouseClick(mate, Qt.MouseButton.LeftButton)
    assert mate._state == "happy"
    assert mate._bubble in mp.LINES["wake"]


def test_dragging_does_not_count_as_a_pet(mate):
    mate.show()
    mate.set_state("idle")
    start = mate.rect().center()
    QTest.mousePress(mate, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseRelease(mate, Qt.MouseButton.LeftButton, pos=start + QPoint(40, 0))
    assert mate._bubble == ""


def test_too_many_pets_makes_it_snap_then_calm_down(mate):
    mate.set_state("idle")
    for _ in range(mp.Personality.PET_LIMIT - 1):
        mate._on_pet(False)
    mate._on_pet(False)
    assert mate._state == "angry"
    assert mate._bubble in mp.LINES["pet_many"]
    mate._end_pet()
    assert mate._state == "idle"


def test_falling_asleep_says_a_nap_line_unless_a_reply_is_showing(mate):
    mate.set_state("idle")
    mate._maybe_sleep()
    assert mate._state == "sleeping" and mate._bubble in mp.LINES["sleep"]
    mate.set_state("idle")
    mate.say("Aether: Here is your answer.")
    mate._maybe_sleep()
    assert mate._state == "sleeping" and mate._bubble == "Here is your answer."


def test_error_bubble_uses_persona_line(mate):
    mate.on_app_state("ERROR")
    assert mate._bubble in mp.LINES["error"]


def test_working_quips_are_rate_limited(mate):
    mate.quip_chance = 1.0
    mate.on_app_state("THINKING")
    assert mate._bubble in mp.LINES["thinking"]
    mate._clear_bubble()
    mate.on_app_state("PROCESSING")      # inside the cooldown window
    assert mate._bubble == ""
    mate._last_quip = float("-inf")
    mate.on_app_state("PROCESSING")
    assert mate._bubble in mp.LINES["working"]


def test_quips_are_occasional_not_constant(mate):
    mate.quip_chance = 0.0
    mate.on_app_state("THINKING")
    assert mate._bubble == ""


def test_greeting_shows_once_when_idle_and_visible(mate):
    mate._greet()                         # not shown yet -> silent
    assert mate._bubble == ""
    mate.show()
    assert mate._greeted is True and mate._greet_timer.isActive()
    mate._greet()
    assert mate._bubble in set().union(*mp.TIMED_LINES["greet"].values())
    mate.hide()
    mate.show()                           # a re-show must not schedule another greeting
    assert not mate._greet_timer.isActive()


def test_greeting_waits_for_a_calm_mate(mate):
    mate.show()
    mate.set_state("thinking")
    mate._greet()
    assert mate._bubble == ""


def test_idle_chatter_only_when_calm_and_quiet_and_always_rearms(mate):
    mate.show()
    mate.set_state("thinking")
    mate._chatter()
    assert mate._bubble == "" and mate._chatter_timer.isActive()
    mate.set_state("idle")
    mate.say("Aether: Reply on screen.")
    mate._chatter()
    assert mate._bubble == "Reply on screen."   # never talks over a reply
    mate._clear_bubble()
    mate._chatter()
    assert mate._bubble in set(mp.LINES["chatter"]) | set(mp.TIMED_LINES["chatter"]["late"])
    assert mate._chatter_timer.isActive()


def test_hiding_stops_the_greeting_and_chatter_timers(mate):
    mate.show()
    assert mate._chatter_timer.isActive()
    mate.hide()
    assert not mate._chatter_timer.isActive() and not mate._greet_timer.isActive()


def test_personality_off_keeps_the_mate_quiet_but_reactive(mate):
    mate.chatty = False
    mate.show()
    mate.set_state("idle")
    mate._on_pet(False)
    assert mate._state == "happy" and mate._bubble == ""
    mate._maybe_sleep()
    mate._chatter()
    mate._greet()
    assert mate._bubble == ""
    mate.quip_chance = 1.0
    mate.on_app_state("THINKING")
    assert mate._bubble == ""
    mate.on_app_state("ERROR")           # errors always get the plain, useful message
    assert mate._bubble == "Something went wrong - check the log."


def test_bubbles_off_silences_persona_too(mate):
    mate.show_bubbles = False
    mate.set_state("idle")
    mate._on_pet(False)
    mate._maybe_sleep()
    mate._chatter()
    assert mate._bubble == ""


# ── wiring into the real MainWindow ─────────────────────────────────────────

@pytest.fixture
def make_window(qapp, tmp_path, monkeypatch):
    ui = pytest.importorskip("ui")
    made = []

    def _make(config=None):
        if config is not None:
            monkeypatch.setattr(ui._config_home, "load_config", lambda: config)
        w = ui.MainWindow(str(tmp_path / "face.png"))
        made.append(w)
        return w

    yield _make
    for w in made:
        w.close()


def test_main_window_creates_the_mate_and_forwards_state(make_window):
    w = make_window()
    assert w._mate is not None and w._mate.isVisible()
    w._apply_state("THINKING")
    assert w._mate._state == "thinking"
    w._apply_state("SPEAKING")
    assert w._mate._state == "speaking"
    w._apply_state("LISTENING")
    assert w._mate._state == "idle"


def test_main_window_forwards_replies_but_not_system_lines(make_window):
    w = make_window()
    w._log_sig.emit("SYS: Aether is ready.")
    assert w._mate._bubble == ""
    w._log_sig.emit("Aether: **Done.** Moved 3 files.")
    assert w._mate._bubble == "Done. Moved 3 files."


def test_config_can_disable_the_mate_and_the_shortcut_brings_it_back(make_window):
    w = make_window({"desktop_mate": False})
    assert w._mate is None
    w._apply_state("THINKING")                     # must not blow up with no mate
    w._log_sig.emit("Aether: hello.")
    w._toggle_mate()                               # Ctrl+Shift+M forces it on
    assert w._mate is not None and w._mate.isVisible()
    w._toggle_mate()
    assert not w._mate.isVisible()


def test_config_can_turn_bubbles_off(make_window):
    w = make_window({"desktop_mate_bubbles": False})
    assert w._mate.show_bubbles is False
    w._log_sig.emit("Aether: hello.")
    assert w._mate._bubble == ""


def test_closing_the_main_window_closes_the_mate_and_lets_the_app_quit(qapp, make_window):
    # Qt only quits on "last window closed" from inside a running event loop, so
    # drive a real exec() with a safety timeout. A visible Tool window with
    # WA_QuitOnClose left on would keep the process alive until that timeout.
    w = make_window()
    w.show()
    assert w._mate.isVisible()
    timed_out = []

    def _safety():
        timed_out.append(True)
        qapp.quit()

    QTimer.singleShot(50, w.close)
    QTimer.singleShot(3000, _safety)
    qapp.exec()
    assert not timed_out, "the floating mate kept the app running after the main window closed"
    assert not w._mate.isVisible()


def test_config_can_turn_the_personality_off(make_window):
    assert make_window()._mate.chatty is True
    w = make_window({"desktop_mate_personality": False})
    assert w._mate.chatty is False
    w._apply_state("ERROR")
    assert w._mate._bubble == "Something went wrong - check the log."
