"""tools/live_grader.py -- grade a headless replay against tests/live_test_cases.json.

Pure functions (no Aether imports) so they are unit-testable. Used by
tools/verify_local.py; can also be run on its own after a replay:

    python -m tools.live_grader
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests" / "live_test_cases.json"
TURNS = ROOT / "logs" / "live_turns.jsonl"
CRASH_DIR = ROOT / "logs" / "crashes"

# Appearing in ANY reply is a failure, whatever the case says.
GLOBAL_BAD_REPLY = re.compile(r"<tool_call|</tool_call>|\w+\(category=|Traceback|object has no attribute")


def load_cases(path: Path = CASES) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def write_prompts(cases: list[dict], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(c["prompt"] for c in cases) + "\n", encoding="utf-8")
    return dest


def load_turns(since_ts: float, path: Path = TURNS) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if r.get("ts", 0) >= since_ts:
            rows.append(r)
    return rows


def _search(pat: str, text: str) -> bool:
    return re.search(pat, text, re.IGNORECASE | re.MULTILINE) is not None


def grade_case(case: dict, turn: dict | None) -> list[str]:
    """Return a list of failure reasons (empty = pass)."""
    if turn is None:
        return ["no turn recorded (replay stopped early or prompt skipped)"]
    fails: list[str] = []
    reply = turn.get("reply_preview", "") or ""
    if "tier" in case and turn.get("tier") != case["tier"]:
        fails.append(f"tier {turn.get('tier')} != expected {case['tier']}")
    if case.get("reply") and not _search(case["reply"], reply):
        fails.append(f"reply missing /{case['reply']}/")
    if case.get("reply_any") and not _search(case["reply_any"], reply):
        fails.append(f"reply has none of /{case['reply_any']}/")
    if case.get("reply_also") and not _search(case["reply_also"], reply):
        fails.append(f"reply missing /{case['reply_also']}/")
    if case.get("reply_not") and _search(case["reply_not"], reply):
        fails.append(f"reply matched forbidden /{case['reply_not']}/")
    if GLOBAL_BAD_REPLY.search(reply):
        fails.append("reply contains raw tool-call / traceback text")
    if case.get("not_exhausted") and turn.get("exhausted"):
        fails.append("tool loop exhausted without a reply")
    lim = case.get("max_latency_ms")
    if lim and turn.get("latency_ms", 0) > lim:
        fails.append(f"latency {turn['latency_ms']:.0f}ms > {lim}ms")
    return fails


def grade(cases: list[dict], turns: list[dict]) -> list[tuple[dict, dict | None, list[str]]]:
    """Pair cases with turns in order, checking the prompt really lines up."""
    results = []
    for i, c in enumerate(cases):
        t = turns[i] if i < len(turns) else None
        fails = grade_case(c, t)
        if t is not None and not c["prompt"].startswith(t.get("user_preview", "")[:60]):
            fails.insert(0, f"turn misaligned: got '{t.get('user_preview','')[:40]}'")
        results.append((c, t, fails))
    return results


def render(results, new_crashes: int = 0, stderr_tracebacks: int = 0) -> tuple[str, bool]:
    lines, ok = [], True
    for c, t, fails in results:
        mark = "PASS" if not fails else "FAIL"
        ok &= not fails
        lat = f"{t['latency_ms']:.0f}ms" if t else "-"
        lines.append(f"{mark}  {c['id']:<20} {t.get('tier','-') if t else '-':<13} {lat:>8}")
        for f in fails:
            lines.append(f"        - {f}")
            if t:
                lines.append(f"          reply: {t.get('reply_preview','')[:160]!r}")
    if new_crashes:
        ok = False
        lines.append(f"FAIL  {new_crashes} new crash report(s) in logs/crashes/")
    if stderr_tracebacks:
        ok = False
        lines.append(f"FAIL  {stderr_tracebacks} traceback(s) in replay stderr")
    n_fail = sum(1 for _, _, f in results if f)
    lines.append(f"\n{len(results) - n_fail}/{len(results)} cases passed" + ("" if ok else "  -> FAILURES"))
    return "\n".join(lines), ok


def count_crashes() -> int:
    return len(list(CRASH_DIR.glob("*.json"))) if CRASH_DIR.exists() else 0


# ── Whole-run checks ─────────────────────────────────────────────────────
# Unlike grade_case (one reply vs. one expectation) these look across ALL turns,
# stderr and the console. Severity: FAIL breaks the run, WARN is reported only.

MAX_HARD_TOOLS_WARN = 20      # HARD tier sends the full catalog; > this squeezes the context budget
MEDIAN_LATENCY_MS_WARN = 3000
P95_LATENCY_MS_FAIL = 60000
MIN_MEMORY_HIT_RATE_WARN = 0.5


def _pct(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))]


def grade_overall(cases: list[dict], turns: list[dict], stderr: str = "") -> list[tuple[str, str, str]]:
    """Return [(severity, check_name, detail)] for every check that did NOT pass.

    severity is "FAIL" or "WARN". An empty list means all whole-run checks passed.
    """
    out: list[tuple[str, str, str]] = []

    def fail(name, detail): out.append(("FAIL", name, detail))
    def warn(name, detail): out.append(("WARN", name, detail))

    if len(turns) != len(cases):
        fail("turn_count", f"{len(turns)} turns recorded for {len(cases)} prompts")
    if not turns:
        return out

    n_exh = sum(1 for t in turns if t.get("exhausted"))
    if n_exh:
        fail("no_exhausted_loops", f"{n_exh} turn(s) ended with the tool loop exhausted")
    n_trunc = sum(1 for t in turns if t.get("truncated"))
    if n_trunc:
        warn("no_truncation", f"{n_trunc} reply(ies) cut off at the generation limit")

    bad = [t["user_preview"][:40] for t in turns if GLOBAL_BAD_REPLY.search(t.get("reply_preview", ""))]
    if bad:
        fail("no_raw_tool_text", f"raw tool-call/traceback text in replies to: {bad}")

    # Stale-answer bleed: one long reply given to two DIFFERENT prompts.
    seen: dict[str, str] = {}
    for t in turns:
        r = (t.get("reply_preview") or "").strip()
        if len(r) < 40:
            continue
        key, prompt = r[:80], t.get("user_preview", "")
        if key in seen and seen[key] != prompt:
            fail("no_stale_replies", f"same reply for different prompts: {seen[key][:30]!r} / {prompt[:30]!r}")
        seen.setdefault(key, prompt)

    lat = sorted(float(t.get("latency_ms", 0)) for t in turns)
    if _pct(lat, 0.5) > MEDIAN_LATENCY_MS_WARN:
        warn("median_latency", f"median {_pct(lat, 0.5):.0f}ms > {MEDIAN_LATENCY_MS_WARN}ms")
    if _pct(lat, 0.95) > P95_LATENCY_MS_FAIL:
        fail("p95_latency", f"p95 {_pct(lat, 0.95):.0f}ms > {P95_LATENCY_MS_FAIL}ms")

    cheap = [t["user_preview"][:30] for t in turns
             if t.get("tier") in ("QUICK", "QUICK_INTENT") and t.get("tools_sent", 0) > 0]
    if cheap:
        fail("quick_tiers_send_no_tools", f"QUICK/QUICK_INTENT turns that carried tools: {cheap}")

    hard_tools = [t.get("tools_sent", 0) for t in turns if t.get("tier") == "HARD"]
    if hard_tools and max(hard_tools) > MAX_HARD_TOOLS_WARN:
        warn("hard_tool_bloat", f"HARD tier sent {max(hard_tools)} tools (> {MAX_HARD_TOOLS_WARN}); "
                                "schema alone overruns the 3000-token message budget")

    mem = [t["memory_hit"] for t in turns if t.get("memory_hit") is not None]
    if mem and sum(mem) / len(mem) < MIN_MEMORY_HIT_RATE_WARN:
        warn("memory_hit_rate", f"{sum(mem)}/{len(mem)} memory-checked turns hit")

    tiers = {t.get("tier") for t in turns}
    for need in ("QUICK", "QUICK_INTENT", "EVERYDAY", "HARD"):
        if need not in tiers:
            warn("tier_coverage", f"no {need} turn in this run -- that path went untested")

    n_err = len(re.findall(r"[—-] ERR:", stderr))
    if n_err:
        fail("no_tool_errors", f"{n_err} 'ERR:' line(s) in stderr")
    n_gem = stderr.count("Gemini failed")
    if n_gem:
        warn("cloud_route_healthy", f"{n_gem} Gemini failure(s) -- fell back to local; check model name/quota")
    if "clamping message budget" in stderr:
        warn("tool_schema_budget", "smart_context clamped the message budget (tool schema > token budget)")
    wrn = [m for m in re.findall(r"WRN: ([^\n]{0,90})", stderr) if "Gemini failed" not in m]
    if wrn:
        warn("warnings_in_log", f"{len(wrn)} WRN line(s), e.g. {wrn[0]!r}")
    return out


def render_overall(items: list[tuple[str, str, str]]) -> tuple[str, bool]:
    if not items:
        return "OVERALL  all whole-run checks passed", True
    lines = [f"{sev}  {name}: {detail}" for sev, name, detail in items]
    ok = not any(sev == "FAIL" for sev, _, _ in items)
    lines.append("OVERALL  " + ("passed with warnings" if ok else "FAILED"))
    return "\n".join(lines), ok


if __name__ == "__main__":
    cs = load_cases()
    ts = load_turns(0)[-len(cs):]
    text, ok = render(grade(cs, ts))
    print(text)
    t2, ok2 = render_overall(grade_overall(cs, ts))
    print("\n" + t2)
    sys.exit(0 if (ok and ok2) else 1)
