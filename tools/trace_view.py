"""python -m tools.trace_view - read the flight recorder (logs/trace/*.jsonl).

  list                      sessions, newest first
  summary [SESSION]         counts, slowest things, errors, tools used
  show    [SESSION]         every event in order   (--kind K --grep TEXT --limit N)
  turn    TURN [SESSION]    one user request end to end (e.g. t0003)
  errors  [SESSION]         only failures, with tracebacks

SESSION is a session id (20261030-170200), a path, or omitted for the latest.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "logs" / "trace"


def _files(trace_dir: Path, session: str | None) -> list[Path]:
    if session and Path(session).exists():
        p = Path(session)
        return [p] if p.is_file() else sorted(p.glob("trace-*.jsonl"))
    if not session:
        latest = trace_dir / "latest.txt"
        if latest.exists():
            session = latest.read_text(encoding="utf-8").strip()
        else:
            ids = sorted({p.name[6:21] for p in trace_dir.glob("trace-*.jsonl")})
            session = ids[-1] if ids else ""
    if not session:
        return []
    parts = sorted(trace_dir.glob(f"trace-{session}*.jsonl"),
                   key=lambda p: (len(p.name), p.name))
    return parts


def load(trace_dir: Path, session: str | None) -> list[dict]:
    events: list[dict] = []
    for f in _files(trace_dir, session):
        with open(f, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    events.append(json.loads(ln))
                except json.JSONDecodeError:
                    events.append({"kind": "corrupt_line", "raw": ln[:200]})
    return events


def pair_spans(ev: list[dict]) -> list[dict]:
    """Copy the fields of each <kind>.start (tool name, args...) onto its .end event,
    matched by span id, so an end event can be read on its own."""
    starts = {e["span"]: e for e in ev if str(e.get("kind", "")).endswith(".start") and "span" in e}
    out = []
    for e in ev:
        k = str(e.get("kind", ""))
        if k.endswith(".end") and e.get("span") in starts:
            st = starts[e["span"]]
            e = {**{f: v for f, v in st.items()
                    if f not in ("seq", "ts", "t", "thread", "kind", "span")}, **e}
        out.append(e)
    return out


def _is_error(e: dict) -> bool:
    k = e.get("kind", "")
    if e.get("ok") is False:
        return True
    if k in ("uncaught_exception", "crash", "stall_dump", "trace_dropped", "http_error",
             "llm_error", "stt_error", "speak_failed", "tool_exception", "subprocess_error"):
        return True
    if k == "log" and e.get("level") in ("ERROR", "CRITICAL", "WARNING"):
        return True
    return k.endswith("_error")


def _brief(v, n=160) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n] + "…"


def fmt(e: dict, full: bool = False) -> str:
    skip = {"seq", "ts", "t", "thread", "kind", "turn", "active_turn"}
    head = f"{e.get('seq', '-'):>6} {str(e.get('ts', ''))[11:23]} +{e.get('t', 0):>9.3f}s " \
           f"{e.get('turn', e.get('active_turn', '')):<6} {e.get('kind', '?')}"
    rest = {k: v for k, v in e.items() if k not in skip}
    if full:
        body = json.dumps(rest, ensure_ascii=False, indent=2, default=str)
        return head + "\n" + "\n".join("        " + ln for ln in body.splitlines())
    return head + "  " + "  ".join(f"{k}={_brief(v)}" for k, v in rest.items()
                                   if k != "traceback")


def cmd_list(a) -> int:
    ids = sorted({p.name[6:21] for p in a.dir.glob("trace-*.jsonl")}, reverse=True)
    if not ids:
        print(f"no traces in {a.dir}")
        return 1
    for sid in ids:
        size = sum(p.stat().st_size for p in a.dir.glob(f"trace-{sid}*.jsonl"))
        print(f"{sid}  {size / 1024:>9.1f} KiB")
    return 0


def cmd_show(a) -> int:
    ev = load(a.dir, a.session)
    if not ev:
        print("no events found")
        return 1
    if a.kind:
        ev = [e for e in ev if any(e.get("kind", "").startswith(k) for k in a.kind)]
    if a.grep:
        g = a.grep.lower()
        ev = [e for e in ev if g in json.dumps(e, ensure_ascii=False, default=str).lower()]
    for e in ev[: a.limit or None]:
        print(fmt(e, a.full))
    return 0


def cmd_turn(a) -> int:
    ev = [e for e in load(a.dir, a.session) if (e.get("turn") or e.get("active_turn")) == a.turn]
    if not ev:
        print(f"no events for {a.turn}")
        return 1
    for e in ev:
        print(fmt(e, a.full))
    return 0


def cmd_errors(a) -> int:
    ev = [e for e in load(a.dir, a.session) if _is_error(e)]
    for e in ev:
        print(fmt(e, True))
    if not ev:
        print("no errors recorded")
    return 0


def cmd_summary(a) -> int:
    ev = load(a.dir, a.session)
    if not ev:
        print("no events found")
        return 1
    ev = pair_spans(ev)
    kinds = Counter(e.get("kind", "?") for e in ev)
    turns = [e for e in ev if e.get("kind") == "turn_end"]
    print(f"events: {len(ev)}   span: {ev[0].get('ts', '')} -> {ev[-1].get('ts', '')}")
    print(f"turns: {len(turns)}   failed: {sum(1 for t in turns if not t.get('ok', True))}")
    print("\nevents by kind:")
    for k, n in kinds.most_common():
        print(f"  {n:>6}  {k}")
    tools = Counter(e.get("tool") for e in ev if e.get("kind") == "tool.end")
    if tools:
        print("\ntools used:")
        for k, n in tools.most_common():
            print(f"  {n:>6}  {k}")
    timed = [e for e in ev if isinstance(e.get("dur_ms"), (int, float))
             and e.get("kind") not in ("turn_end",)]
    if timed:
        print("\nslowest operations:")
        for e in sorted(timed, key=lambda x: -x["dur_ms"])[:10]:
            print(f"  {e['dur_ms']:>10.1f} ms  {e.get('kind')}  "
                  f"{_brief(e.get('tool') or e.get('url') or e.get('model') or '', 60)}"
                  f"  seq={e.get('seq')}")
    errs = [e for e in ev if _is_error(e)]
    print(f"\nerrors/warnings: {len(errs)}  (run `errors` to see them)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="trace_view", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="trace directory")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    for name, fn in (("summary", cmd_summary), ("show", cmd_show), ("errors", cmd_errors)):
        s = sub.add_parser(name)
        s.add_argument("session", nargs="?")
        if name == "show":
            s.add_argument("--kind", action="append", help="kind prefix, repeatable")
            s.add_argument("--grep")
            s.add_argument("--limit", type=int, default=0)
            s.add_argument("--full", action="store_true", help="pretty-print every field")
        s.set_defaults(fn=fn)
    t = sub.add_parser("turn")
    t.add_argument("turn")
    t.add_argument("session", nargs="?")
    t.add_argument("--full", action="store_true")
    t.set_defaults(fn=cmd_turn)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
