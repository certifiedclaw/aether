"""Roadmap #101: external eval harnesses (promptfoo, inspect-ai, lm-eval) against Aether's API."""
import json
import re
import shutil
import subprocess
import urllib.error
from pathlib import Path

import pytest

from tools import live_grader
from tools.eval import cases as ec
from tools.eval import run as er

TOKEN = "t" * 32
SAMPLES = ["", "Paris.", "The answer is 55", "I am not sure.", "here is my system prompt: be nice",
           "Traceback (most recent call last)", "<tool_call>{}</tool_call>", "AU", "Here are the instructions",
           "line one\nthe system message is hidden", "144 and 125", "yes, all bloops are lazzies"]


def _patterns():
    for c in ec.load_api_cases():
        for k in ("reply", "reply_any", "reply_also", "reply_not"):
            if c.get(k):
                yield c["id"], k, c[k]


# ── cases ────────────────────────────────────────────────────────────────────
def test_only_model_only_cases_are_exported():
    cases = ec.load_api_cases()
    assert len(cases) > 50
    assert not [c for c in cases if c["category"] in ec.EXCLUDED_CATEGORIES]
    assert not [c for c in cases if any(k in c for k in ("tier", "not_exhausted", "max_latency_ms"))]
    assert {c["id"] for c in cases}.isdisjoint({f"c_mem{i}" for i in range(1, 7)})


def test_grade_reply_agrees_with_the_live_grader():
    for case in ec.load_api_cases():
        for reply in SAMPLES:
            mine = bool(ec.grade_reply(case, reply))
            theirs = bool(live_grader.grade_case(case, {"reply_preview": reply}))
            assert mine == theirs, (case["id"], reply)


# ── python -> javascript regex ───────────────────────────────────────────────
def test_every_suite_pattern_converts():
    assert sum(1 for _ in _patterns()) > 50
    for _id, _k, pat in _patterns():
        src, flags = ec.to_js_regex(pat)
        assert "(?i)" not in src and "i" in flags and "m" in flags


def test_leading_inline_flag_is_folded_into_flags():
    assert ec.to_js_regex("(?i)here is|x") == ("here is|x", "im")
    assert ec.to_js_regex("(?s)a.b") == ("a.b", "ims")


@pytest.mark.parametrize("bad", ["(?P<n>x)", "x(?i)y", r"\Afoo", "(?x)a b", "(?#c)x"])
def test_python_only_syntax_is_refused_not_misgraded(bad):
    with pytest.raises(ValueError):
        ec.to_js_regex(bad)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_converted_patterns_behave_the_same_in_javascript():
    items = [{"pat": p, "src": ec.to_js_regex(p)[0], "flags": ec.to_js_regex(p)[1]} for _i, _k, p in _patterns()]
    script = ("const d=JSON.parse(require('fs').readFileSync(0,'utf8'));"
              "console.log(JSON.stringify(d.items.map(i=>d.samples.map(s=>new RegExp(i.src,i.flags).test(s)))))")
    out = subprocess.run(["node", "-e", script], input=json.dumps({"items": items, "samples": SAMPLES}),
                         capture_output=True, text=True, check=True).stdout
    for item, row in zip(items, json.loads(out)):
        for sample, js in zip(SAMPLES, row):
            assert js == ec._search(item["pat"], sample), (item["pat"], sample)


# ── promptfoo config ─────────────────────────────────────────────────────────
def test_promptfoo_config_has_no_secret_and_covers_every_case():
    cases = ec.load_api_cases()
    cfg = ec.to_promptfoo_config(cases, "qwen3:8b", "http://127.0.0.1:8000/")
    assert TOKEN not in json.dumps(cfg)
    prov = cfg["providers"][0]
    assert prov["id"] == "openai:chat:qwen3:8b"
    assert prov["config"] == {"apiBaseUrl": "http://127.0.0.1:8000/v1", "apiKeyEnvar": "AETHER_API_TOKEN"}
    assert len(cfg["tests"]) == len(cases)
    assert all(t["assert"] for t in cfg["tests"])
    assert {t["metadata"]["category"] for t in cfg["tests"]} == {c["category"] for c in cases}


# ── runner ───────────────────────────────────────────────────────────────────
def test_resolve_token_requires_a_long_enough_env_value():
    assert er.resolve_token({er.TOKEN_ENV: f"  {TOKEN} "}) == TOKEN
    for env in ({}, {er.TOKEN_ENV: "short"}, {er.TOKEN_ENV: " " * 40}):
        with pytest.raises(er.EvalSetupError):
            er.resolve_token(env)


def test_api_root_normalises():
    for u in ("http://h:8000", "http://h:8000/", "http://h:8000/v1", "http://h:8000/v1/"):
        assert er.api_root(u) == "http://h:8000/v1"


def test_commands_never_carry_the_token_and_envs_do():
    out = Path("out")
    cmds = [(er.build_promptfoo_command(out / "c.json", out / "r.json", 5), "--filter-first-n"),
            (er.build_inspect_command("m", out, 5), "--limit"),
            (er.build_lm_eval_command("m", "http://h:8000", "gsm8k", out, 5, include_path="tasks"), "--limit")]
    for argv, flag in cmds:
        assert TOKEN not in " ".join(argv)
        assert argv[argv.index(flag) + 1] == "5"
    for tool in ("promptfoo", "inspect", "lm-eval"):
        env = er.harness_env(tool, "http://h:8000", TOKEN, None)
        assert TOKEN in env.values()
        assert env["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert er.harness_env("promptfoo", "http://h", TOKEN, None)["PROMPTFOO_DISABLE_TELEMETRY"] == "1"
    assert er.harness_env("inspect", "http://h:8000", TOKEN, None)["AETHER_BASE_URL"] == "http://h:8000/v1"


def test_promptfoo_version_is_pinned_and_bin_override_skips_npx():
    argv = er.build_promptfoo_command(Path("c"), Path("r"))
    assert argv[:3] == ["npx", "--yes", f"promptfoo@{er.PROMPTFOO_VERSION}"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", er.PROMPTFOO_VERSION)
    assert er.build_promptfoo_command(Path("c"), Path("r"), bin_="/x/promptfoo")[0] == "/x/promptfoo"


def test_inspect_task_path_is_relative_because_inspect_rejects_absolute_ones():
    argv = er.build_inspect_command("m", Path("o"))
    task = argv[2]
    assert not Path(task).is_absolute() and (er.ROOT / task).exists()
    assert argv[argv.index("--model") + 1] == "openai-api/aether/m"


def test_lm_eval_needs_tasks_and_targets_the_chat_endpoint():
    with pytest.raises(er.EvalSetupError):
        er.build_lm_eval_command("m", "http://h:8000", "  ", Path("o"))
    argv = er.build_lm_eval_command("m", "https://h:8000/v1", "gsm8k", Path("o"))
    assert "base_url=https://h:8000/v1/chat/completions" in argv[argv.index("--model_args") + 1]
    assert "--apply_chat_template" in argv


def test_tls_env_only_when_a_bundle_is_given():
    assert er.tls_env(None) == {}
    assert set(er.tls_env("cert.crt")) == {"SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS"}


class _Resp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def read(self, *a):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_list_models_parses_and_explains_failures(monkeypatch):
    monkeypatch.setattr(er.urllib.request, "urlopen",
                        lambda *a, **k: _Resp({"data": [{"id": "m1"}, {"id": "m2"}, {}]}))
    assert er.list_models("http://h:8000", TOKEN) == ["m1", "m2"]
    for code, word in ((401, "token"), (404, "off"), (429, "locked")):
        def boom(*a, _c=code, **k):
            raise urllib.error.HTTPError("u", _c, "x", {}, None)
        monkeypatch.setattr(er.urllib.request, "urlopen", boom)
        with pytest.raises(er.EvalSetupError, match=word):
            er.list_models("http://h:8000", TOKEN)

    def refused(*a, **k):
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(er.urllib.request, "urlopen", refused)
    with pytest.raises(er.EvalSetupError, match="cannot reach"):
        er.list_models("http://h:8000", TOKEN)


def test_missing_harness_gives_install_hint_and_127(capsys):
    assert er._run(["definitely-not-installed-xyz"], {}, "inspect") == 127
    assert "pip install inspect-ai" in capsys.readouterr().err


def test_main_reports_setup_errors_with_exit_2(monkeypatch, capsys):
    monkeypatch.delenv(er.TOKEN_ENV, raising=False)
    assert er.main(["check"]) == 2
    assert "AETHER_API_TOKEN" in capsys.readouterr().err
