"""tools/eval_harness.py + the AETHER_EVAL_OVERRIDES hook: no model, no Aether process needed."""
import io
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


def test_tool_call_cases_are_supported_by_the_everyday_router():
    from core.tool_declarations import TOOL_DECLARATIONS, _to_ollama_tools
    from core.tool_router import filter_tools

    declarations = _to_ollama_tools(TOOL_DECLARATIONS)
    cases = eh.load_tool_cases()
    assert len(cases) >= 4
    for case in cases:
        available = {
            (tool.get("function") or {}).get("name")
            for tool in filter_tools(declarations, case["prompt"])
        }
        assert case["tool"] in available, case["id"]
        for pattern in case["argument_patterns"].values():
            re.compile(pattern)


def test_grade_tool_call_requires_one_correct_schema_shaped_call():
    case = {
        "id": "tool-test", "tool": "weather_report",
        "argument_patterns": {"city": "Paris"},
    }
    response = {"message": {"tool_calls": [{
        "function": {"name": "weather_report", "arguments": {"city": "Paris, France"}},
    }]}}
    row = eh.grade_tool_call(case, response, 12.5)
    assert row["pass"] and row["tool"] == "weather_report"
    assert row["latency_ms"] == 12

    response["message"]["tool_calls"].append(response["message"]["tool_calls"][0])
    assert not eh.grade_tool_call(case, response, 1)["pass"]
    wrong_tool = {"message": {"tool_calls": [{
        "function": {"name": "web_search", "arguments": {"city": "Paris"}},
    }]}}
    assert not eh.grade_tool_call(case, wrong_tool, 1)["pass"]
    assert not eh.grade_tool_call(case, {"message": {"content": "It is sunny."}}, 1)["pass"]


def test_tool_call_suite_uses_live_schemas_and_never_dispatches(monkeypatch):
    monkeypatch.setattr(eh, "model_installed", lambda *a, **k: (True, "ok"))
    seen = []
    args_by_id = {
        "tc_web_search": {"query": "Python 3.14 official release notes"},
        "tc_weather": {"city": "Paris, France"},
        "tc_file_list": {"action": "list", "path": "Downloads"},
        "tc_browser_navigate": {"action": "go_to", "url": "https://example.com"},
        "tc_reminder": {"date": "2026-10-20", "time": "15:30", "message": "check email"},
        "tc_send_message": {
            "receiver": "Alex", "message_text": "I will arrive at 3:30.",
            "platform": "WhatsApp",
        },
        "tc_volume": {"action": "volume_set", "value": "40"},
        "tc_youtube": {"action": "play", "query": "Take On Me by a-ha"},
    }

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        prompt = body["messages"][1]["content"]
        case = next(c for c in eh.load_tool_cases() if c["prompt"] == prompt)
        names = {(t.get("function") or {}).get("name") for t in body["tools"]}
        assert case["tool"] in names
        assert timeout == 8 and request.full_url == "http://ollama.test/api/chat"
        seen.append(body)
        answer = {
            "message": {"content": "", "tool_calls": [{
                "function": {
                    "name": case["tool"],
                    "arguments": args_by_id[case["id"]],
                },
            }]},
        }
        return io.BytesIO(json.dumps(answer).encode("utf-8"))

    monkeypatch.setattr(eh.urllib.request, "urlopen", fake_urlopen)
    result = eh.run_tool_call_suite("safe-tools", "qwen2.5:1.5b", "http://ollama.test/", 8)
    assert result["passed"] == result["n"] == 8
    assert result["suite"] == "tool_calls"
    assert result["execution_policy"].endswith("none were executed.")
    assert len(seen) == result["n"]
    assert all(request["options"] == {"temperature": 0} for request in seen)
    assert all("tools" in request for request in seen)


def test_tool_call_suite_records_backend_errors(monkeypatch):
    import urllib.error

    monkeypatch.setattr(eh, "model_installed", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(
        eh.urllib.request, "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("offline")),
    )
    result = eh.run_tool_call_suite("offline", "qwen2.5:1.5b", None, 1)
    assert result["passed"] == 0 and result["exit_code"] == 1
    assert all("Ollama request failed" in row["fails"][0] for row in result["cases"])


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
        monkeypatch.setenv("AETHER_CHAT_HISTORY_PATH", env["AETHER_CHAT_HISTORY_PATH"])
        from core.chat_history import ChatSessionStore
        isolated_store = ChatSessionStore()
        assert isolated_store.active_messages == []
        isolated_store.save_current([{"role": "user", "content": "temporary replay"}])
        seen["chat_path"] = isolated_store.path
        output = (
            f"[Aether] >>> (2) timed out after 30s — continuing\n"
            f"[Aether] stdin closed — {len(cases)} line(s) sent.\n"
        )
        return 0, output, "", 1.0

    monkeypatch.setattr(vl, "_MemoryIsolation", NoIso)
    monkeypatch.setattr(eh, "model_installed", lambda url, model, timeout=5.0: (True, "ok"))
    monkeypatch.setattr(live_grader, "load_turns",
                        lambda since, path=None: [_turn(c["prompt"], "x") for c in cases])
    res = eh.run_suite("t", "core", "llama3.2:3b", "http://10.0.0.5:11434", 30, runner=fake_run)
    ov = json.loads(seen["env"]["AETHER_EVAL_OVERRIDES"])
    assert ov["llm_model"] == "llama3.2:3b" and ov["llm_url"] == "http://10.0.0.5:11434"
    assert seen["env"]["AETHER_HEADLESS"] == "1" and seen["env"]["AETHER_SKIP_MAINTENANCE"] == "1"
    assert seen["env"]["AETHER_CHAT_HISTORY_PATH"] == str(seen["chat_path"])
    assert not seen["chat_path"].exists()
    assert seen["prompts"] == [c["prompt"] for c in cases] and seen["timeout"] == 30 * len(cases) + 120
    assert res["n"] == len(cases) and res["model"] == "llama3.2:3b"
    assert res["replay"] == {"lines_sent": len(cases), "timed_out_lines": [2]}
    assert "replay_prompt_timeouts" in {w["check"] for w in res["whole_run"]}
    assert "main.py replay" in eh.render_result(res)


def test_run_suite_can_replay_selected_case_ids(monkeypatch):
    from tools import verify_local as vl

    class NoIso:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    seen = {}

    def fake_run(cmd, timeout, env=None, stdin_path=None):
        seen["prompts"] = stdin_path.read_text(encoding="utf-8").splitlines()
        return 0, "[Aether] stdin closed — 1 line(s) sent.\n", "", 1.0

    monkeypatch.setattr(vl, "_MemoryIsolation", NoIso)
    monkeypatch.setattr(
        live_grader, "load_turns",
        lambda since, path=None: [_turn("Spell drawer backwards.", "reward")],
    )
    result = eh.run_suite(
        "selected", "core", None, None, 30, runner=fake_run,
        case_ids=["c_instr6"],
    )
    assert seen["prompts"] == ["Spell drawer backwards."]
    assert [case["id"] for case in result["cases"]] == ["c_instr6"]
    assert result["replay"]["lines_sent"] == 1
    with pytest.raises(ValueError, match="unknown case id"):
        eh.run_suite("bad-case", "core", None, None, 30, case_ids=["not-a-case"])


def test_run_suite_can_disable_model_guidance_for_control(monkeypatch):
    from tools import verify_local as vl

    class NoIso:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    seen = {}

    def fake_run(cmd, timeout, env=None, stdin_path=None):
        seen["env"] = env
        return 0, "", "", 1.0

    monkeypatch.setattr(vl, "_MemoryIsolation", NoIso)
    monkeypatch.setattr(
        live_grader, "load_turns",
        lambda since, path=None: [_turn("Spell drawer backwards.", "reward")],
    )
    eh.run_suite(
        "control", "core", "qwen2.5:1.5b", None, 30,
        runner=fake_run, case_ids=["c_instr6"], disable_model_guidance=True,
    )

    assert seen["env"]["AETHER_EVAL_DISABLE_MODEL_GUIDANCE"] == "1"


def test_run_suite_refuses_when_model_missing(monkeypatch):
    monkeypatch.setattr(eh, "model_installed", lambda *a, **k: (False, "not installed"))
    with pytest.raises(RuntimeError, match="not installed"):
        eh.run_suite("t", "core", "nope:1b", None, 30, runner=lambda *a, **k: None)


# ── the config override hook ──────────────────────────────────────────────────
def test_override_hook_applies_only_allowlisted_string_keys(monkeypatch):
    from core import llm_client as lc
    monkeypatch.setenv("AETHER_EVAL_OVERRIDES", json.dumps(
        {"llm_model": "tiny:1b", "llm_quick_model": "tiny:1b",
         "gemini_api_key": "STOLEN", "llm_url": 5, "llm_provider": "ollama"}))
    base = {"llm_model": "big:8b", "gemini_api_key": "real"}
    out = lc._apply_eval_overrides(base)
    assert out["llm_model"] == "tiny:1b" and out["llm_provider"] == "ollama"
    assert out["llm_quick_model"] == "tiny:1b"
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


def test_model_prompt_guidance_can_be_disabled_only_for_evaluation(monkeypatch):
    from core.prompt_builder import model_prompt_guidance

    monkeypatch.setenv("AETHER_EVAL_DISABLE_MODEL_GUIDANCE", "1")
    monkeypatch.delenv("AETHER_EVAL_OVERRIDES", raising=False)
    assert model_prompt_guidance("qwen2.5:1.5b")

    monkeypatch.setenv("AETHER_EVAL_OVERRIDES", '{"llm_model":"qwen2.5:1.5b"}')
    assert model_prompt_guidance("qwen2.5:1.5b") == ""


def test_list_survives_results_with_missing_metadata(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(eh, "RESULTS_DIR", tmp_path)
    r = _result("old", ["4", "6", "Paris"])
    r.pop("suite"); r.pop("model")
    eh.save_result(r)
    assert eh.main(["list"]) == 0
    assert "old" in capsys.readouterr().out
