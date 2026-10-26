"""The Gemini model dropdown asks Google which models the key can use.

Regression for a live log: gemini-2.5-pro and gemini-2.5-flash-lite answered 404 for
the user's key but stayed in the hardcoded dropdown, and each pick rested for an hour.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core import gemini_models as gm

STATIC = [
    ("gemini-3.8-flash",      "Gemini 3.8 Flash - recommended"),
    ("gemini-2.5-flash",      "Gemini 2.5 Flash - retired"),
    ("gemini-2.5-pro",        "Gemini 2.5 Pro"),
    ("gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite"),
    ("gemini-2.0-flash",      "Gemini 2.0 Flash"),
]


def ids(rows):
    return [m for m, _ in rows]


# ── parsing /models ─────────────────────────────────────────────────────────

def test_parse_openai_shape_strips_models_prefix_and_keeps_order():
    payload = {"data": [{"id": "models/gemini-3.8-flash"}, {"id": "models/gemini-2.5-pro"}]}
    assert gm.parse_model_list(payload) == ["gemini-3.8-flash", "gemini-2.5-pro"]


def test_parse_also_accepts_googles_native_shape_and_bare_lists():
    assert gm.parse_model_list({"models": [{"name": "models/gemini-2.5-pro"}]}) == ["gemini-2.5-pro"]
    assert gm.parse_model_list(["models/gemini-2.5-pro", "gemini-2.5-flash"]) == ["gemini-2.5-pro", "gemini-2.5-flash"]


def test_parse_drops_non_chat_and_non_gemini_models_and_duplicates():
    payload = {"data": [{"id": "models/gemini-2.5-pro"}, {"id": "models/gemini-2.5-pro"},
                        {"id": "models/text-embedding-004"}, {"id": "models/gemini-embedding-001"},
                        {"id": "models/imagen-3.0-generate"}, {"id": "models/gemini-2.5-flash-preview-tts"},
                        {"id": "models/gemini-2.0-flash-live-001"}, {"id": "models/gemma-3-27b-it"},
                        {"id": "models/gemini-2.5-flash-image"}]}
    assert gm.parse_model_list(payload) == ["gemini-2.5-pro"]


@pytest.mark.parametrize("junk", [None, {}, [], "text", 5, {"data": None}, {"data": "x"},
                                  {"data": [None, 3, {}, {"id": None}]}])
def test_parse_tolerates_garbage(junk):
    assert gm.parse_model_list(junk) == []


# ── merging into dropdown rows ──────────────────────────────────────────────

def test_no_live_list_keeps_the_static_list():
    assert gm.merge_choices(STATIC, None) == STATIC


def test_live_list_removes_models_the_key_cannot_use():
    rows = gm.merge_choices(STATIC, ["gemini-3.8-flash", "gemini-2.5-flash"])
    assert ids(rows) == ["gemini-3.8-flash", "gemini-2.5-flash"]
    assert "gemini-2.5-pro" not in ids(rows) and "gemini-2.5-flash-lite" not in ids(rows)


def test_known_models_keep_their_friendly_labels_and_static_order():
    rows = gm.merge_choices(STATIC, ["gemini-2.0-flash", "gemini-3.8-flash"])
    assert rows == [STATIC[0], STATIC[4]]


def test_new_models_google_adds_appear_newest_first_without_snapshots():
    live = ["gemini-3.8-flash", "gemini-4.1-pro", "gemini-3.9-flash", "gemini-3.9-flash-preview-09-2026",
            "gemini-2.5-pro-exp-03-25", "gemini-flash-latest", "gemini-3.9-flash-001"]
    rows = gm.merge_choices(STATIC, live)
    assert ids(rows) == ["gemini-3.8-flash", "gemini-4.1-pro", "gemini-3.9-flash"]
    assert dict(rows)["gemini-4.1-pro"] == "Gemini 4.1 Pro"


def test_snapshots_are_shown_when_nothing_else_is_left():
    rows = gm.merge_choices(STATIC, ["gemini-9.0-flash-preview-01-2027"])
    assert ids(rows) == ["gemini-9.0-flash-preview-01-2027"]


def test_empty_live_answer_never_leaves_an_empty_dropdown():
    assert gm.merge_choices(STATIC, []) == STATIC


def test_saved_model_the_key_cannot_use_stays_visible_and_flagged_first():
    rows = gm.merge_choices(STATIC, ["gemini-3.8-flash"], current="gemini-2.5-pro")
    assert rows[0] == ("gemini-2.5-pro", "Gemini 2.5 Pro — not available for your key")
    assert ids(rows)[1:] == ["gemini-3.8-flash"]


def test_saved_custom_model_is_not_dropped_even_offline():
    rows = gm.merge_choices(STATIC, None, current="gemini-9.9-custom")
    assert rows[0][0] == "gemini-9.9-custom" and rows[0][1].endswith("— saved")
    assert rows[1:] == STATIC


def test_usable_saved_model_is_not_duplicated_or_flagged():
    rows = gm.merge_choices(STATIC, ["gemini-3.8-flash", "gemini-2.5-pro"], current="models/gemini-2.5-pro")
    assert ids(rows).count("gemini-2.5-pro") == 1
    assert all("not available" not in label for _, label in rows)


def test_pretty_label():
    assert gm.pretty_label("gemini-2.5-flash-lite") == "Gemini 2.5 Flash Lite"


# ── the network call ─────────────────────────────────────────────────────────

class Resp:
    def __init__(self, status=200, body=None, boom=False):
        self.status_code, self._body, self._boom = status, body, boom

    def json(self):
        if self._boom:
            raise ValueError("not json")
        return self._body


def test_fetch_sends_the_key_as_a_bearer_token_to_the_models_endpoint():
    seen = {}

    def get(url, headers=None, timeout=None):
        seen.update(url=url, headers=headers, timeout=timeout)
        return Resp(200, {"data": [{"id": "models/gemini-3.8-flash"}]})

    assert gm.fetch_available("  KEY123 ", timeout=2, get=get) == ["gemini-3.8-flash"]
    assert seen["url"] == gm.GEMINI_BASE_URL + "/models"
    assert seen["headers"] == {"Authorization": "Bearer KEY123"} and seen["timeout"] == 2


@pytest.mark.parametrize("resp", [Resp(401), Resp(403), Resp(404), Resp(503),
                                  Resp(200, {"data": []}), Resp(200, boom=True)])
def test_fetch_returns_none_on_any_failure(resp):
    assert gm.fetch_available("k", get=lambda *a, **k: resp) is None


def test_fetch_returns_none_on_network_errors_and_without_a_key():
    def down(*a, **k):
        raise ConnectionError("offline")

    assert gm.fetch_available("k", get=down) is None

    def never(*a, **k):
        raise AssertionError("must not call out without a key")

    assert gm.fetch_available("", get=never) is None and gm.fetch_available("   ", get=never) is None


def test_the_models_url_matches_the_one_the_chat_client_uses():
    from core.llm_client import GEMINI_BASE_URL
    assert gm.GEMINI_BASE_URL == GEMINI_BASE_URL


# ── the settings dialog ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    # Hold the reference: an app that is only a temporary gets garbage-collected and
    # the next QWidget() aborts the whole test process.
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    try:
        return QApplication.instance() or QApplication([])
    except Exception:
        pytest.skip("no usable Qt environment")


@pytest.fixture
def overlay(qapp, monkeypatch):
    try:
        import ui
    except Exception:
        pytest.skip("ui could not be imported in this environment")
    # The dialog must never hit the network in tests, whatever it decides to do on build.
    monkeypatch.setattr(gm, "fetch_available", lambda *a, **k: None)
    made = []

    def make(**initial):
        ov = ui.SetupOverlay(mode="config", initial=initial)
        made.append(ov)
        return ov

    yield make
    for ov in made:
        ov.close()


def combo_ids(ov):
    return [ov._gemini_model_combo.itemData(i) for i in range(ov._gemini_model_combo.count())]


def test_dialog_starts_with_the_builtin_list_and_selects_the_saved_model(overlay):
    ov = overlay(llm_provider="gemini", llm_model="gemini-2.5-pro")
    assert combo_ids(ov) == [m for m, _ in ov._GEMINI_MODELS]
    assert ov._gemini_model_combo.currentData() == "gemini-2.5-pro"


def test_live_list_replaces_the_dropdown_and_keeps_the_selection(overlay):
    ov = overlay(llm_provider="gemini", llm_model="gemini-3.8-flash")
    ov._on_gemini_models(["gemini-3.8-flash", "gemini-2.5-flash"])
    assert combo_ids(ov) == ["gemini-3.8-flash", "gemini-2.5-flash"]
    assert ov._gemini_model_combo.currentData() == "gemini-3.8-flash"
    assert ov._gemini_refresh_btn.isEnabled() and ov._gemini_refresh_btn.text() == "↻"


def test_a_dead_saved_model_is_flagged_not_silently_swapped(overlay):
    ov = overlay(llm_provider="gemini", llm_model="gemini-2.5-pro")
    ov._on_gemini_models(["gemini-3.8-flash"])
    assert ov._gemini_model_combo.currentData() == "gemini-2.5-pro"
    assert "not available for your key" in ov._gemini_model_combo.currentText()


def test_a_failed_lookup_falls_back_to_the_builtin_list_and_says_so(overlay):
    ov = overlay(llm_provider="gemini", llm_model="gemini-3.8-flash")
    ov._on_gemini_models(["gemini-3.8-flash"])
    ov._on_gemini_models(None)
    assert combo_ids(ov) == [m for m, _ in ov._GEMINI_MODELS]
    assert "built-in list" in ov._gemini_refresh_btn.toolTip()


def test_users_choice_survives_a_refresh(overlay):
    ov = overlay(llm_provider="gemini", llm_model="gemini-3.8-flash")
    ov._on_gemini_models(["gemini-3.8-flash", "gemini-2.5-flash", "gemini-2.0-flash"])
    ov._gemini_model_combo.setCurrentIndex(combo_ids(ov).index("gemini-2.0-flash"))
    ov._on_gemini_models(["gemini-3.8-flash", "gemini-2.5-flash", "gemini-2.0-flash"])
    assert ov._gemini_model_combo.currentData() == "gemini-2.0-flash"


def test_refresh_without_any_key_does_nothing_and_never_calls_out(overlay, monkeypatch):
    import threading
    started = []
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self))
    from core import credentials
    monkeypatch.setattr(credentials, "get_secret", lambda *a, **k: "")
    ov = overlay(llm_provider="gemini")
    ov._gemini_key_input.setText("")
    ov._refresh_gemini_models()
    assert not started and ov._gemini_refresh_btn.isEnabled()


def test_refresh_with_a_key_runs_in_a_worker_thread_and_reports_back(overlay, monkeypatch):
    import threading
    ov = overlay(llm_provider="gemini")
    got = []
    ov._gemini_models_sig.connect(got.append)
    monkeypatch.setattr(gm, "fetch_available", lambda key, *a, **k: ["gemini-3.8-flash"] if key == "AIzaTEST" else None)
    ov._gemini_key_input.setText("AIzaTEST")
    threads = []
    real_start = threading.Thread.start
    monkeypatch.setattr(threading.Thread, "start", lambda self: (threads.append(self), real_start(self))[1])
    ov._refresh_gemini_models()
    assert not ov._gemini_refresh_btn.isEnabled()          # busy while it runs
    for t in threads:
        t.join(2)
    from PyQt6.QtWidgets import QApplication
    QApplication.processEvents()        # a worker-thread emit is queued for the GUI thread
    assert got == [["gemini-3.8-flash"]]
    assert ov._gemini_refresh_btn.isEnabled()                # re-enabled by the slot
    assert ov._gemini_model_combo.itemData(0) == "gemini-3.8-flash"


def test_a_saved_key_triggers_one_lookup_on_open(overlay, monkeypatch):
    from PyQt6.QtCore import QTimer
    calls = []
    monkeypatch.setattr(QTimer, "singleShot", staticmethod(lambda ms, fn: calls.append(fn)))
    ov = overlay(llm_provider="gemini", gemini_api_key="AIzaSAVED")
    assert [c.__name__ for c in calls].count("_refresh_gemini_models") == 1
    calls.clear()
    overlay(llm_provider="ollama", gemini_api_key="AIzaSAVED")      # not using Gemini: no lookup
    assert "_refresh_gemini_models" not in [c.__name__ for c in calls]
    calls.clear()
    overlay(llm_provider="gemini")                                  # no key: no lookup
    assert "_refresh_gemini_models" not in [c.__name__ for c in calls]


def test_submitting_saves_the_selected_model(overlay):
    ov = overlay(llm_provider="gemini", llm_model="gemini-3.8-flash", gemini_api_key="AIzaSAVED")
    ov._on_gemini_models(["gemini-3.8-flash", "gemini-2.5-flash"])
    ov._gemini_model_combo.setCurrentIndex(1)
    out = []
    ov.done.connect(out.append)
    ov._submit()
    import json
    assert out and json.loads(out[0])["llm_model"] == "gemini-2.5-flash"
