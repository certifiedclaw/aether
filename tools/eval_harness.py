"""tools/eval_harness.py -- score Aether on fixed prompts, per model, and compare runs.

Built on tools/live_grader.py (the same cases + grader verify_local uses) and on the headless
stdin replay. What it adds: pick the model/host for one run WITHOUT editing config
(AETHER_EVAL_OVERRIDES, honoured by core/llm_client), per-category scores, latency stats,
a saved JSON result per run, and a regression-aware comparison between two runs.

    python -m tools.eval_harness run --model qwen3:8b --label qwen3-8b
    python -m tools.eval_harness run --model llama3.2:3b --suite all --label l3b
    python -m tools.eval_harness run --suite core --case-id c_instr6 --model qwen2.5:1.5b --label reverse
    python -m tools.eval_harness run-tools --model qwen2.5:1.5b --label qwen-tools
    python -m tools.eval_harness compare qwen3-8b l3b          # exit 1 if l3b regressed
    python -m tools.eval_harness list
    python -m tools.eval_harness show qwen3-8b

Suites: smoke (tests/live_test_cases.json), core (tests/eval_suite_core.json), all (both).
A run replaces nothing of yours: memory is isolated for the replay and restored afterwards,
and chat history is redirected to a temporary file that is deleted after the run.
`run-tools` calls Ollama directly with Aether's routed schemas and grades the
returned tool calls without dispatching them; it uses temperature 0 for more
repeatable comparisons.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import live_grader  # noqa: E402

RESULTS_DIR = ROOT / "logs" / "eval"
SUITES = {
    "smoke": [live_grader.CASES],
    "core": [ROOT / "tests" / "eval_suite_core.json"],
    "all": [live_grader.CASES, ROOT / "tests" / "eval_suite_core.json"],
}
DEFAULT_PER_CASE_S = 90          # CPU-only hosts are slow; override with --per-case
TOOL_CASES = ROOT / "tests" / "eval_suite_tool_calls.json"


# pure helpers (unit-tested; no Aether imports, no subprocess)
def load_suite(name: str) -> list[dict]:
    if name not in SUITES:
        raise ValueError(f"unknown suite {name!r}; choose from {sorted(SUITES)}")
    cases: list[dict] = []
    for path in SUITES[name]:
        cases += live_grader.load_cases(path)
    ids = [c["id"] for c in cases]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate case ids: {sorted(dupes)}")
    return cases


def load_tool_cases() -> list[dict]:
    """Load the real-schema tool-call cases, kept separate from replay suites."""
    cases = json.loads(TOOL_CASES.read_text(encoding="utf-8"))["cases"]
    ids = [case.get("id") for case in cases]
    if not cases or len(ids) != len(set(ids)):
        raise ValueError("tool-call suite must contain cases with unique ids")
    for case in cases:
        if not all(case.get(key) for key in ("id", "prompt", "tool", "argument_patterns")):
            raise ValueError(f"invalid tool-call case: {case.get('id')!r}")
    return cases


def _tool_call_args(call: dict) -> tuple[str, dict]:
    """Return the function name and arguments from one Ollama tool call."""
    fn = call.get("function") if isinstance(call, dict) else None
    if not isinstance(fn, dict):
        return "", {}
    args = fn.get("arguments", {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (ValueError, TypeError):
            return str(fn.get("name") or ""), {}
    return str(fn.get("name") or ""), args if isinstance(args, dict) else {}


def grade_tool_call(case: dict, response: dict, latency_ms: float) -> dict:
    """Grade a model response without dispatching or executing its tool calls."""
    message = response.get("message") if isinstance(response, dict) else None
    calls = message.get("tool_calls", []) if isinstance(message, dict) else []
    message_data = message if isinstance(message, dict) else {}
    calls = calls if isinstance(calls, list) else []
    failures = []
    if len(calls) != 1:
        failures.append(f"expected exactly one tool call, got {len(calls)}")
    tool_name, arguments = _tool_call_args(calls[0]) if calls else ("", {})
    if calls and tool_name != case["tool"]:
        failures.append(f"expected tool {case['tool']!r}, got {tool_name!r}")
    if calls:
        call = calls[0]
        function = call.get("function") if isinstance(call, dict) else None
        raw_arguments = function.get("arguments", {}) if isinstance(function, dict) else None
        if not isinstance(raw_arguments, (dict, str)):
            failures.append("tool arguments were not an object")
    for key, pattern in case["argument_patterns"].items():
        value = arguments.get(key)
        if value is None or re.search(pattern, str(value), re.IGNORECASE) is None:
            failures.append(f"argument {key!r} did not match /{pattern}/")
    return {
        "id": case["id"], "category": "tool_call",
        "pass": not failures, "fails": failures,
        "tool": tool_name, "arguments": arguments,
        "latency_ms": round(latency_ms),
        "reply": str(message_data.get("content") or "")[:300],
        "done_reason": response.get("done_reason") if isinstance(response, dict) else None,
    }


def run_tool_call_suite(label: str, model: str, url: str | None,
                        per_case_s: int) -> dict:
    """Ask Ollama for tool calls using Aether's schemas; never execute them."""
    from core.tool_declarations import TOOL_DECLARATIONS, _to_ollama_tools
    from core.tool_router import filter_tools

    endpoint = (url or "http://localhost:11434").rstrip("/")
    ok, msg = model_installed(endpoint, model)
    if not ok:
        raise RuntimeError(msg)

    all_tools = _to_ollama_tools(TOOL_DECLARATIONS)
    cases = load_tool_cases()
    rows = []
    for case in cases:
        tools = filter_tools(all_tools, case["prompt"])
        available = {
            (tool.get("function") or {}).get("name")
            for tool in tools
        }
        if case["tool"] not in available:
            raise ValueError(
                f"tool-call case {case['id']!r} expects {case['tool']!r}, "
                "but Aether's everyday router does not expose it"
            )
        request_data = json.dumps({
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are Aether. Use the available function tool for "
                        "the user's requested action. Return exactly one tool "
                        "call with arguments matching its schema. Do not answer "
                        "in prose instead of calling the tool."
                    ),
                },
                {"role": "user", "content": case["prompt"]},
            ],
            "tools": tools,
            "options": {"temperature": 0},
            "stream": False,
        }).encode("utf-8")
        request = urllib.request.Request(
            endpoint + "/api/chat",
            data=request_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=per_case_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
            rows.append(grade_tool_call(case, payload, (time.monotonic() - started) * 1000))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            rows.append({
                "id": case["id"], "category": "tool_call", "pass": False,
                "fails": [f"Ollama request failed: {type(exc).__name__}: {exc}"],
                "tool": "", "arguments": {},
                "latency_ms": round((time.monotonic() - started) * 1000),
                "reply": "",
            })

    latencies = [row["latency_ms"] for row in rows]
    passed = sum(row["pass"] for row in rows)
    return {
        "label": label, "suite": "tool_calls", "model": model, "url": url,
        "temperature": 0,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "seconds": round(sum(latencies) / 1000),
        "exit_code": 0 if passed == len(rows) else 1,
        "python": platform.python_version(), "platform": platform.platform(),
        "n": len(rows), "passed": passed,
        "score": round(passed / len(rows), 4) if rows else 0.0,
        "categories": {"tool_call": {
            "n": len(rows), "passed": passed,
            "score": round(passed / len(rows), 4) if rows else 0.0,
        }},
        "latency_ms": {
            "p50": round(_pct(latencies, 0.5)),
            "p95": round(_pct(latencies, 0.95)),
            "mean": round(sum(latencies) / len(latencies)) if latencies else 0,
        },
        "whole_run": [], "cases": rows,
        "execution_policy": "Tool calls were recorded and graded; none were executed.",
    }


def _pct(vals: list[float], q: float) -> float:
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))] if s else 0.0


def build_result(cases: list[dict], turns: list[dict], meta: dict) -> dict:
    """Grade `turns` against `cases` and return the JSON-serialisable run result."""
    graded = live_grader.grade(cases, turns)
    rows, cats = [], {}
    for case, turn, fails in graded:
        cat = case.get("category", "other")
        ok = not fails
        c = cats.setdefault(cat, {"n": 0, "passed": 0})
        c["n"] += 1
        c["passed"] += ok
        rows.append({
            "id": case["id"], "category": cat, "pass": ok, "fails": fails,
            "tier": (turn or {}).get("tier"),
            "latency_ms": (turn or {}).get("latency_ms"),
            "reply": ((turn or {}).get("reply_preview") or "")[:300],
        })
    lat = [float(t["latency_ms"]) for t in turns if t.get("latency_ms") is not None]
    passed = sum(r["pass"] for r in rows)
    return {
        **meta,
        "n": len(rows), "passed": passed,
        "score": round(passed / len(rows), 4) if rows else 0.0,
        "categories": {k: {**v, "score": round(v["passed"] / v["n"], 4)} for k, v in sorted(cats.items())},
        "latency_ms": {"p50": round(_pct(lat, 0.5)), "p95": round(_pct(lat, 0.95)),
                       "mean": round(sum(lat) / len(lat)) if lat else 0},
        "whole_run": [{"severity": s, "check": n, "detail": d}
                      for s, n, d in live_grader.grade_overall(cases, turns)],
        "cases": rows,
    }


def compare(a: dict, b: dict, latency_tolerance: float = 0.25) -> dict:
    """Diff run `b` (candidate) against run `a` (baseline)."""
    ra = {r["id"]: r for r in a["cases"]}
    rb = {r["id"]: r for r in b["cases"]}
    common = [i for i in ra if i in rb]
    regress = [i for i in common if ra[i]["pass"] and not rb[i]["pass"]]
    fixed = [i for i in common if not ra[i]["pass"] and rb[i]["pass"]]
    cats = {}
    for cat in sorted(set(a["categories"]) | set(b["categories"])):
        sa = a["categories"].get(cat, {}).get("score")
        sb = b["categories"].get(cat, {}).get("score")
        cats[cat] = (sa, sb)
    la, lb = a["latency_ms"]["p50"], b["latency_ms"]["p50"]
    slower = bool(la) and lb > la * (1 + latency_tolerance)
    return {
        "baseline": a.get("label"), "candidate": b.get("label"),
        "score": (a["score"], b["score"]),
        "regressions": regress, "fixes": fixed,
        "only_in_baseline": [i for i in ra if i not in rb],
        "only_in_candidate": [i for i in rb if i not in ra],
        "categories": cats, "p50_latency_ms": (la, lb), "slower": slower,
        "regressed": bool(regress) or b["score"] < a["score"],
    }


def render_result(r: dict) -> str:
    out = [f"== {r.get('label')}  model={r.get('model') or '(config default)'}  suite={r.get('suite')} ==",
           f"score {r['passed']}/{r['n']} = {r['score']:.0%}   "
           f"latency p50 {r['latency_ms']['p50']}ms  p95 {r['latency_ms']['p95']}ms", "", "by category:"]
    for cat, v in r["categories"].items():
        out.append(f"  {cat:<12} {v['passed']:>2}/{v['n']:<2} {v['score']:.0%}")
    replay = r.get("replay")
    if replay:
        sent = replay.get("lines_sent")
        sent_text = "unknown prompts sent" if sent is None else f"{sent} prompts sent"
        timed_out = replay.get("timed_out_lines", [])
        out.append(f"\nmain.py replay: {sent_text}; "
                   + (f"timed out on input line(s) {timed_out}" if timed_out else "no input timeouts"))
    bad = [c for c in r["cases"] if not c["pass"]]
    if bad:
        out.append("\nfailed cases:")
        for c in bad:
            out.append(f"  {c['id']:<18} {'; '.join(c['fails'])[:110]}")
            if r.get("suite") == "tool_calls":
                out.append(
                    f"  {'':<18} call: {c.get('tool') or '(none)'} "
                    f"{json.dumps(c.get('arguments') or {}, ensure_ascii=False)}"
                )
            else:
                out.append(f"  {'':<18} reply: {c['reply'][:110]!r}")
    for w in r.get("whole_run", []):
        out.append(f"\n{w['severity']} {w['check']}: {w['detail']}")
    return "\n".join(out)


def render_compare(d: dict) -> str:
    out = [f"baseline {d['baseline']}  ->  candidate {d['candidate']}",
           f"score {d['score'][0]:.0%} -> {d['score'][1]:.0%}   "
           f"p50 latency {d['p50_latency_ms'][0]}ms -> {d['p50_latency_ms'][1]}ms"
           + ("  (SLOWER)" if d["slower"] else ""), "", "by category:"]
    for cat, (sa, sb) in d["categories"].items():
        fa = "-" if sa is None else f"{sa:.0%}"
        fb = "-" if sb is None else f"{sb:.0%}"
        out.append(f"  {cat:<12} {fa:>5} -> {fb:<5}")
    out.append(f"\nregressions ({len(d['regressions'])}): {', '.join(d['regressions']) or 'none'}")
    out.append(f"fixes       ({len(d['fixes'])}): {', '.join(d['fixes']) or 'none'}")
    if d["only_in_baseline"] or d["only_in_candidate"]:
        out.append(f"not comparable: only in baseline {d['only_in_baseline']}, only in candidate {d['only_in_candidate']}")
    out.append("VERDICT: " + ("REGRESSED" if d["regressed"] else "no regression"))
    return "\n".join(out)


def result_path(label: str) -> Path:
    safe = "".join(ch for ch in label if ch.isalnum() or ch in "-_.")
    if not safe:
        raise ValueError("label must contain letters or digits")
    return RESULTS_DIR / f"{safe}.json"


def save_result(r: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    p = result_path(r["label"])
    p.write_text(json.dumps(r, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def load_result(label: str) -> dict:
    p = result_path(label)
    if not p.exists():
        raise FileNotFoundError(f"no saved run {label!r} (looked for {p})")
    return json.loads(p.read_text(encoding="utf-8"))


# running a suite (needs a working Aether install; not unit-tested)
def model_installed(url: str, model: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Ollama only: is `model` present? Returns (ok, message)."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=timeout) as r:
            names = [m.get("name", "") for m in json.loads(r.read().decode()).get("models", [])]
    except Exception as e:  # noqa: BLE001
        return False, f"cannot reach Ollama at {url}: {type(e).__name__}: {e}"
    want = model if ":" in model else model + ":latest"
    if want in names:
        return True, "ok"
    return False, f"{model!r} is not installed at {url}. Installed: {', '.join(names) or 'none'}. Run: ollama pull {model}"


def run_suite(label: str, suite: str, model: str | None, url: str | None, per_case_s: int,
              runner=None, case_ids: list[str] | None = None,
              disable_model_guidance: bool = False) -> dict:
    """Replay the suite headless and return the graded result. `runner` is injectable for tests."""
    from tools import verify_local as vl  # heavy-ish import kept lazy

    cases = load_suite(suite)
    if case_ids:
        requested = set(case_ids)
        available = {case["id"] for case in cases}
        unknown = requested - available
        if unknown:
            raise ValueError(f"unknown case id(s) for suite {suite!r}: {', '.join(sorted(unknown))}")
        cases = [case for case in cases if case["id"] in requested]
    overrides = {}
    if model:
        overrides["llm_model"] = model
    if url:
        overrides["llm_url"] = url
        overrides["ollama_url"] = url
    if model:
        ok, msg = model_installed(url or "http://localhost:11434", model)
        if not ok:
            raise RuntimeError(msg)
    prompts = live_grader.write_prompts(cases, ROOT / "logs" / f"eval_prompts_{suite}.txt")
    env = dict(os.environ, AETHER_HEADLESS="1", AETHER_HEADLESS_STDIN="1",
               AETHER_HEADLESS_STDIN_TIMEOUT=str(per_case_s), PYTHONIOENCODING="utf-8",
               AETHER_SKIP_MAINTENANCE="1")
    if overrides:
        env["AETHER_EVAL_OVERRIDES"] = json.dumps(overrides)
    if disable_model_guidance:
        env["AETHER_EVAL_DISABLE_MODEL_GUIDANCE"] = "1"
    started = time.time()
    timeout = per_case_s * len(cases) + 120
    run = runner or vl.run
    with tempfile.TemporaryDirectory(prefix="aether-eval-") as chat_dir:
        env["AETHER_CHAT_HISTORY_PATH"] = str(Path(chat_dir) / "chat_history.json")
        with vl._MemoryIsolation():
            rc, so, se, dt = run([sys.executable, "main.py"], timeout, env=env, stdin_path=prompts)
    turns = live_grader.load_turns(started)
    meta = {
        "label": label, "suite": suite, "model": model, "url": url,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)),
        "seconds": round(dt), "exit_code": rc,
        "python": platform.python_version(), "platform": platform.platform(),
    }
    result = build_result(cases, turns, meta)
    sent = re.findall(r"stdin closed\s+[—-]\s+(\d+)\s+line\(s\) sent", so or "")
    timeouts = [int(n) for n in re.findall(r"\[Aether\] >>> \((\d+)\) timed out after", so or "")]
    result["replay"] = {
        "lines_sent": int(sent[-1]) if sent else None,
        "timed_out_lines": timeouts,
    }
    if sent and int(sent[-1]) != len(cases):
        result["whole_run"].append({
            "severity": "FAIL", "check": "replay_prompt_count",
            "detail": f"Aether main.py sent {sent[-1]} prompts for {len(cases)} cases",
        })
    if timeouts:
        result["whole_run"].append({
            "severity": "FAIL", "check": "replay_prompt_timeouts",
            "detail": f"Aether main.py timed out waiting for turns on input line(s): {timeouts}",
        })
    if "Traceback (most recent call last)" in (se or ""):
        result["whole_run"].append({"severity": "FAIL", "check": "stderr_traceback",
                                    "detail": f"{se.count('Traceback (most recent call last)')} traceback(s) in stderr"})
    if rc == -999:
        result["whole_run"].append({"severity": "FAIL", "check": "timeout",
                                    "detail": f"replay hit the {timeout}s limit; use --per-case to raise it"})
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval_harness", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="replay a suite against a model and save the result")
    r.add_argument("--label", required=True, help="name for this run, e.g. qwen3-8b")
    r.add_argument("--model", help="Ollama model for this run only (default: your config)")
    r.add_argument("--url", help="Ollama URL for this run only, e.g. a remote PC")
    r.add_argument("--suite", default="smoke", choices=sorted(SUITES))
    r.add_argument("--case-id", action="append", help="run only this case id; may be repeated")
    r.add_argument("--per-case", type=int, default=DEFAULT_PER_CASE_S, help="seconds allowed per prompt")
    r.add_argument("--disable-model-guidance", action="store_true",
                   help="run the control without model-specific prompt additions")
    rt = sub.add_parser("run-tools", help="benchmark real model tool calls without executing them")
    rt.add_argument("--label", required=True, help="name for this run, e.g. qwen-tools")
    rt.add_argument("--model", required=True, help="Ollama model for this run")
    rt.add_argument("--url", help="Ollama URL, e.g. a remote PC")
    rt.add_argument("--per-case", type=int, default=DEFAULT_PER_CASE_S, help="seconds allowed per prompt")
    c = sub.add_parser("compare", help="diff two saved runs (exit 1 on regression)")
    c.add_argument("baseline")
    c.add_argument("candidate")
    s = sub.add_parser("show", help="print a saved run")
    s.add_argument("label")
    sub.add_parser("list", help="list saved runs")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "run":
            res = run_suite(a.label, a.suite, a.model, a.url, a.per_case,
                            case_ids=a.case_id,
                            disable_model_guidance=a.disable_model_guidance)
            print(f"saved {save_result(res)}\n")
            print(render_result(res))
            return 0 if res["score"] == 1.0 and not any(w["severity"] == "FAIL" for w in res["whole_run"]) else 1
        if a.cmd == "run-tools":
            res = run_tool_call_suite(a.label, a.model, a.url, a.per_case)
            print(f"saved {save_result(res)}\n")
            print(render_result(res))
            return 0 if res["score"] == 1.0 else 1
        if a.cmd == "compare":
            d = compare(load_result(a.baseline), load_result(a.candidate))
            print(render_compare(d))
            return 1 if d["regressed"] else 0
        if a.cmd == "show":
            print(render_result(load_result(a.label)))
            return 0
        if a.cmd == "list":
            for p in sorted(RESULTS_DIR.glob("*.json")) if RESULTS_DIR.exists() else []:
                d = json.loads(p.read_text(encoding="utf-8"))
                print(f"{d.get('label', p.stem):<24} {d.get('model') or '-':<22} {d.get('suite', '-'):<6} "
                      f"{d['passed']}/{d['n']}  p50 {d['latency_ms']['p50']}ms  {d.get('started', '-')}")
            return 0
    except (RuntimeError, ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
