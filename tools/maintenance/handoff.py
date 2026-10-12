"""
Session handoff log.

The problem this solves: work on Aether happens across separate LLM
sessions/instances that share no memory with each other (and may not even
be the same LLM). Without a deliberate handoff, each new session either
re-derives context from scratch or — worse — silently redoes/undoes prior
work. This is a lightweight, append-only log: each session that does real
work adds one entry before signing off, and the next session (any LLM)
reads HANDOFF.md first to know what just happened and what's next.

This is deliberately NOT auto-generated from git history or file diffs —
that tells you *what* changed, not *why*, or what to do next. A human or
LLM has to actually say those things.

Usage:
    python -m tools.maintenance.handoff add \
        --by "Claude (claude.ai chat)" \
        --summary "Built the maintenance toolkit, wired auto-run+push into main.py" \
        --files "tools/maintenance/,main.py,CLAUDE.md" \
        --next "Add HANDOFF.md entries going forward; verify auto-push works with real git auth"

    python -m tools.maintenance.handoff list
    python -m tools.maintenance.handoff render     # writes HANDOFF.md
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from tools.maintenance import REPO_ROOT, MAINT_DATA_DIR, write_if_changed

HANDOFF_JSON = MAINT_DATA_DIR / "handoff.json"


def _load() -> dict:
    if not HANDOFF_JSON.exists():
        return {"next_id": 1, "entries": []}
    return json.loads(HANDOFF_JSON.read_text(encoding="utf-8"))


def _save(data: dict):
    HANDOFF_JSON.write_text(json.dumps(data, indent=2), encoding="utf-8")


def cmd_add(args):
    data = _load()
    entry = {
        "id": data["next_id"],
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "by": args.by or "unspecified",
        "summary": args.summary,
        "files": [f.strip() for f in (args.files or "").split(",") if f.strip()],
        "next_steps": args.next or "",
    }
    data["next_id"] += 1
    data["entries"].append(entry)
    _save(data)
    print(f"Added handoff #{entry['id']}")
    cmd_render(args)


def cmd_list(args):
    data = _load()
    for e in reversed(data["entries"]):
        print(f"#{e['id']} [{e['timestamp']}] {e['by']}: {e['summary']}")


def render_markdown(data: dict) -> str:
    lines = []
    lines.append("# Aether — Session Handoff Log")
    lines.append("")
    lines.append("> **Read this before starting work, especially if you're a fresh "
                  "session or a different LLM with no memory of prior sessions.** "
                  "Most recent entry first. Rendered from "
                  "`tools/maintenance/data/handoff.json` — add entries via "
                  "`python -m tools.maintenance.handoff add`, don't hand-edit this file.")
    lines.append("")
    lines.append("Generated: " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    lines.append("")

    if not data["entries"]:
        lines.append("_No entries yet. Be the first — add one before you finish this session._")
        lines.append("")
        return "\n".join(lines)

    for e in reversed(data["entries"]):
        lines.append(f"## #{e['id']} — {e['timestamp']} — {e['by']}")
        lines.append("")
        lines.append(e["summary"])
        lines.append("")
        if e["files"]:
            lines.append("**Touched:** " + ", ".join(f"`{f}`" for f in e["files"]))
            lines.append("")
        if e["next_steps"]:
            lines.append(f"**Next steps:** {e['next_steps']}")
            lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def cmd_render(args):
    data = _load()
    content = render_markdown(data)
    out_path = REPO_ROOT / "HANDOFF.md"
    if write_if_changed(out_path, content):
        print(f"Wrote {out_path}")
    else:
        print(f"{out_path} unchanged")


def main():
    parser = argparse.ArgumentParser(description="Aether session handoff log")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Add a handoff entry (does this session's sign-off)")
    p_add.add_argument("--by", default=None, help='Who/what did the work, e.g. "Claude (claude.ai chat)", "GPT-5 (Cursor)"')
    p_add.add_argument("--summary", required=True, help="What was actually done this session")
    p_add.add_argument("--files", default="", help="Comma-separated files/dirs touched")
    p_add.add_argument("--next", default="", help="What the next session should do")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="List entries (newest first)")
    p_list.set_defaults(func=cmd_list)

    p_render = sub.add_parser("render", help="Render HANDOFF.md from the log")
    p_render.set_defaults(func=cmd_render)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
