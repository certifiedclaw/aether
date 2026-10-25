"""Desktop mate: reply-bubble text, Aether-state mapping, widget behaviour, and its
wiring into the real MainWindow. Runs on Qt's offscreen platform (no display needed)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QRect, Qt, QTimer
from PyQt6.QtGui import QFontMetrics, QPixmap
from PyQt6.QtWidgets import QApplication

import desktop_mate as dm


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
