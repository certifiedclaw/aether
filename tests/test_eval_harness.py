"""tools/eval_harness.py + the AETHER_EVAL_OVERRIDES hook: no model, no Aether process needed."""
import json
import re
import time

import pytest

from tools import eval_harness as eh
from tools import live_grader


def _turn(prompt, reply, tier="EVERYDAY", latency=1000.0):
    return {"ts": time.time(), "tier": tier, "user_preview": prompt, "reply_preview": reply,
            "latency_ms": latency, "tools_sent": 0, "exhausted": False}


CASES = [
    {"id": "a", "category": "math", "prompt": "What is 2 plus 2?", "reply": r"\b4\b"},
    {"id": "b", "category": "math", "prompt": "What is 3 plus 3?", "reply": r"\b6\b"},
    {"id": "c", "category": "knowledge", "prompt": "Capital of France?", "reply": "Paris"},
]


def _result(label, replies, latency=1000.0):
    turns = [_turn(c["prompt"], r, latency=latency) for c, r in zip(CASES, replies)]
    return eh.build_result(CASES, turns, {"label": label, "suite": "t", "model": "m"})


def test_build_result_scores_and_categories():
    r = _result("x", ["It is 4.", "That is 7.", "Paris"])
    assert (r["n"], r["passed"]) == (3, 2)
    assert r["categories"]["math"] == {"n": 2, "passed": 1, "score": 0.5}
    assert r["categories"]["knowledge"]["score"] == 1.0
    failed = [c for c in r["cases"] if not c["pass"]]
    assert [c["id"] for c in failed] == ["b"] and failed[0]["fails"]


def test_missing_turns_fail_instead_of_vanishing():
    turns = [_turn(CASES[0]["prompt"], "4")]
    r = eh.build_result(CASES, turns, {"label": "x", "suite": "t"})
    assert r["n"] == 3 and r["passed"] == 1
    assert all(not c["pass"] for c in r["cases"][1:])


def test_compare_flags_regressions_fixes_and_slowdown():
    base = _result("base", ["4", "6", "Paris"], latency=1000)
    cand = _result("cand", ["4", "7", "Paris"], latency=2000)
    d = eh.compare(base, cand)
    assert d["regressions"] == ["b"] and d["fixes"] == [] and d["regressed"] and d["slower"]
    assert "REGRESSED" in eh.render_compare(d)
    d2 = eh.compare(cand, base)
    assert d2["fixes"] == ["b"] and not d2["regressed"] and not d2["slower"]
    assert "no regression" in eh.render_compare(d2)


def test_compare_notes_non_overlapping_cases():
    base = _result("base", ["4", "6", "Paris"])
    cand = _result("cand", ["4", "6", "Paris"])
    cand["cases"].append({"id": "extra", "category": "x", "pass": True, "fails": [], "tier": None,
                          "latency_ms": 1, "reply": ""})
    assert eh.compare(base, cand)["only_in_candidate"] == ["extra"]


def test_render_result_lists_failures():
    text = eh.render_result(_result("x", ["4", "nope", "Paris"]))
    assert "2/3" in text and "failed cases" in text and "b " in text


def test_result_label_is_sanitised(tmp_path, monkeypatch):
    monkeypatch.setattr(eh, "RESULTS_DIR", tmp_path)
    p = eh.result_path("../../etc/passwd")
    assert p.parent == tmp_path and "/" not in p.name and ".." not in p.stem.replace(".", "")
    with pytest.raises(ValueError):
        eh.result_path("///")


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(eh, "RESULTS_DIR", tmp_path)
    r = _result("run-1", ["4", "6", "Paris"])
    eh.save_result(r)
    assert eh.load_result("run-1")["score"] == 1.0
    with pytest.raises(FileNotFoundError):
        eh.load_result("nope")


@pytest.mark.parametrize("suite", ["smoke", "core", "all"])
def test_shipped_suites_are_well_formed(suite):
    cases = eh.load_suite(suite)                       # also checks ids are unique
    assert cases
    for c in cases:
        assert c["prompt"].strip() and c["category"] != "other", c["id"]
    assert all("\n" not in c["prompt"] for c in cases)  # one prompt per stdin line


def test_all_suite_is_smoke_plus_core():
    assert len(eh.load_suite("all")) == len(eh.load_suite("smoke")) + len(eh.load_suite("core"))


def test_all_suite_meets_100_case_roadmap_target():
    assert len(eh.load_suite("all")) >= 100


def test_http_expansion_accepts_hyphenated_and_unspaced_forms():
    case = next(c for c in eh.load_suite("core") if c["id"] == "c_know7")
    for reply in ("Hypertext Transfer Protocol", "Hyper Text Transfer Protocol"):
        assert not live_grader.grade_case(case, {"reply_preview": reply})


def test_core_suite_never_needs_a_tool_or_approval():
    """Approval prompts would stall a headless replay; keep the core suite tool-free."""
    risky = ("delete", "remove", "shutdown", "run ", "open ", "volume", "install", "download", "send ")
    for c in eh.load_suite("core"):
        assert not any(w in c["prompt"].lower() for w in risky), c["id"]


def test_all_case_expectation_patterns_compile():
    for case in eh.load_suite("all"):
        for key in ("reply", "reply_any", "reply_also", "reply_not"):
            if key in case:
                re.compile(case[key])


def test_unknown_suite_rejected():
    with pytest.raises(ValueError):
        eh.load_suite("nope")


def test_run_suite_with_fake_runner(monkeypatch):
    from tools import verify_local as vl
    cases = eh.load_suite("core")

    class NoIso:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    seen = {}

    def fake_run(cmd, timeout, env=None, stdin_path=None):
        seen["env"], seen["timeout"] = env, timeout
        seen["prompts"] = stdin_path.read_text(encoding="utf-8").splitlines()
        return 0, "", "", 1.0

    monkeypatch.setattr(vl, "_MemoryIsolation", NoIso)
    monkeypatch.setattr(eh, "model_installed", lambda url, model, timeout=5.0: (True, "ok"))
    monkeypatch.setattr(live_grader, "load_turns",
                        lambda since, path=None: [_turn(c["prompt"], "x") for c in cases])
    res = eh.run_suite("t", "core", "llama3.2:3b", "http://10.0.0.5:11434", 30, runner=fake_run)
    ov = json.loads(seen["env"]["AETHER_EVAL_OVERRIDES"])
    assert ov["llm_model"] == "llama3.2:3b" and ov["llm_url"] == "http://10.0.0.5:11434"
    assert seen["env"]["AETHER_HEADLESS"] == "1" and seen["env"]["AETHER_SKIP_MAINTENANCE"] == "1"
    assert seen["prompts"] == [c["prompt"] for c in cases] and seen["timeout"] == 30 * len(cases) + 120
    assert res["n"] == len(cases) and res["model"] == "llama3.2:3b"


def test_run_suite_refuses_when_model_missing(monkeypatch):
    monkeypatch.setattr(eh, "model_installed", lambda *a, **k: (False, "not installed"))
    with pytest.raises(RuntimeError, match="not installed"):
        eh.run_suite("t", "core", "nope:1b", None, 30, runner=lambda *a, **k: None)


# ── the config override hook ──────────────────────────────────────────────────
def test_override_hook_applies_only_allowlisted_string_keys(monkeypatch):
    from core import llm_client as lc
    monkeypatch.setenv("AETHER_EVAL_OVERRIDES", json.dumps(
        {"llm_model": "tiny:1b", "gemini_api_key": "STOLEN", "llm_url": 5, "llm_provider": "ollama"}))
    base = {"llm_model": "big:8b", "gemini_api_key": "real"}
    out = lc._apply_eval_overrides(base)
    assert out["llm_model"] == "tiny:1b" and out["llm_provider"] == "ollama"
    assert out["gemini_api_key"] == "real" and "llm_url" not in out    # secrets and non-strings ignored
    assert base["llm_model"] == "big:8b"                               # input (the file cache) untouched


@pytest.mark.parametrize("raw", ["", "not json", "[1,2]", "null"])
def test_override_hook_ignores_garbage(monkeypatch, raw):
    from core import llm_client as lc
    monkeypatch.setenv("AETHER_EVAL_OVERRIDES", raw)
    cfg = {"llm_model": "big:8b"}
    assert lc._apply_eval_overrides(cfg) == cfg


def test_override_hook_off_by_default(monkeypatch):
    from core import llm_client as lc
    monkeypatch.delenv("AETHER_EVAL_OVERRIDES", raising=False)
    cfg = {"llm_model": "big:8b"}
    assert lc._apply_eval_overrides(cfg) is cfg


def test_list_survives_results_with_missing_metadata(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(eh, "RESULTS_DIR", tmp_path)
    r = _result("old", ["4", "6", "Paris"])
    r.pop("suite"); r.pop("model")
    eh.save_result(r)
    assert eh.main(["list"]) == 0
    assert "old" in capsys.readouterr().out
