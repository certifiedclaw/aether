"""
Roadmap / backlog tracker.

Unlike the other three maintenance scripts, this one has real hand-curated
state: `tools/maintenance/data/roadmap.json`. That file is the source of
truth; ROADMAP.md is the rendered, human-readable view of it and gets
overwritten every render — edit the backlog via this CLI (or the json
directly), never ROADMAP.md itself.

Usage:
    python -m tools.maintenance.roadmap list
    python -m tools.maintenance.roadmap add "Audit agent/executor.py" --priority high --notes "Never audited per PERFORMANCE_NOTES.md"
    python -m tools.maintenance.roadmap start <id>
    python -m tools.maintenance.roadmap note <id> --notes "Current evidence and next step"
    python -m tools.maintenance.roadmap done <id>
    python -m tools.maintenance.roadmap render      # writes ROADMAP.md
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from tools.maintenance import REPO_ROOT, MAINT_DATA_DIR, write_if_changed

ROADMAP_JSON = MAINT_DATA_DIR / "roadmap.json"
STATUSES = ["backlog", "in-progress", "done"]
PRIORITIES = ["low", "medium", "high"]

SEED_ITEMS = [
    {
        "title": "Audit agent/executor.py",
        "priority": "medium",
        "notes": "Flagged 'not yet audited' in PERFORMANCE_NOTES.md.",
    },
    {
        "title": "Audit agent/planner.py",
        "priority": "medium",
        "notes": "Flagged 'not yet audited' in PERFORMANCE_NOTES.md.",
    },
    {
        "title": "Second pass on agent/task_queue.py",
        "priority": "medium",
        "notes": "Prior session fixed a memory leak in TaskQueue.cancel(); worth revisiting given how much of agent/ is unaudited.",
    },
    {
        "title": "Audit core/observability.py and core/task_scheduler.py",
        "priority": "low",
        "notes": "Flagged 'not yet audited' in PERFORMANCE_NOTES.md.",
    },
    {
        "title": "Check plugin_watcher.py poll interval and rescan scope",
        "priority": "medium",
        "notes": "Hot-reload watcher polls the filesystem — confirm it only rescans changed files, not everything.",
    },
    {
        "title": "Check memory/semantic_memory.py for full-rebuild-on-write issue",
        "priority": "high",
        "notes": "Same sqlite/embedding shape as hybrid_memory.py had before that fix — likely has the same bug.",
    },
]


def _load() -> dict:
    if not ROADMAP_JSON.exists():
        data = {"next_id": 1, "items": []}
        for seed in SEED_ITEMS:
            data["items"].append(_new_item(data, seed["title"], seed["priority"], seed["notes"]))
        _save(data)
        return data
    return json.loads(ROADMAP_JSON.read_text(encoding="utf-8"))


def _save(data: dict):
    ROADMAP_JSON.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _new_item(data: dict, title: str, priority: str, notes: str) -> dict:
    item = {
        "id": data["next_id"],
        "title": title,
        "priority": priority if priority in PRIORITIES else "medium",
        "status": "backlog",
        "notes": notes or "",
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    data["next_id"] += 1
    return item


def cmd_add(args):
    data = _load()
    item = _new_item(data, args.title, args.priority, args.notes or "")
    data["items"].append(item)
    _save(data)
    print(f"Added #{item['id']}: {item['title']}")


def _set_status(item_id: int, status: str):
    data = _load()
    for item in data["items"]:
        if item["id"] == item_id:
            item["status"] = status
            item["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            _save(data)
            print(f"#{item_id} -> {status}")
            return
    print(f"No item with id {item_id}")


def cmd_start(args):
    _set_status(args.id, "in-progress")


def cmd_done(args):
    _set_status(args.id, "done")


def cmd_note(args):
    data = _load()
    for item in data["items"]:
        if item["id"] == args.id:
            item["notes"] = args.notes
            item["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            _save(data)
            print(f"Updated notes for #{args.id}")
            return
    print(f"No item with id {args.id}")


def cmd_list(args):
    data = _load()
    items = data["items"]
    if args.status:
        items = [i for i in items if i["status"] == args.status]
    for item in sorted(items, key=lambda i: (i["status"] != "in-progress", i["status"] == "done", -PRIORITIES.index(i["priority"]))):
        print(f"#{item['id']:<3} [{item['status']:<11}] ({item['priority']:<6}) {item['title']}")


def render_markdown(data: dict) -> str:
    lines = []
    lines.append("# Aether — Roadmap")
    lines.append("")
    lines.append("> **Auto-rendered** from `tools/maintenance/data/roadmap.json`. "
                  "Don't hand-edit this file — use `python -m tools.maintenance.roadmap`.")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    for status, heading in [("in-progress", "## In progress"), ("backlog", "## Backlog"), ("done", "## Done")]:
        items = [i for i in data["items"] if i["status"] == status]
        lines.append(heading)
        lines.append("")
        if not items:
            lines.append("_Nothing here._")
            lines.append("")
            continue
        if status == "backlog":
            items = sorted(items, key=lambda i: -PRIORITIES.index(i["priority"]))
        for item in items:
            box = "x" if status == "done" else " "
            prio = f"`{item['priority']}`" if status != "done" else ""
            line = f"- [{box}] **#{item['id']}** {item['title']}"
            if prio:
                line += f" {prio}"
            lines.append(line)
            if item.get("notes"):
                lines.append(f"  - {item['notes']}")
        lines.append("")

    return "\n".join(lines)


def cmd_render(args):
    data = _load()
    content = render_markdown(data)
    out_path = REPO_ROOT / "ROADMAP.md"
    if write_if_changed(out_path, content):
        print(f"Wrote {out_path}")
    else:
        print(f"{out_path} unchanged")


def main():
    parser = argparse.ArgumentParser(description="Aether roadmap/backlog tracker")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Add a backlog item")
    p_add.add_argument("title")
    p_add.add_argument("--priority", choices=PRIORITIES, default="medium")
    p_add.add_argument("--notes", default="")
    p_add.set_defaults(func=cmd_add)

    p_start = sub.add_parser("start", help="Mark item in-progress")
    p_start.add_argument("id", type=int)
    p_start.set_defaults(func=cmd_start)

    p_note = sub.add_parser("note", help="Update an item's progress notes")
    p_note.add_argument("id", type=int)
    p_note.add_argument("--notes", required=True)
    p_note.set_defaults(func=cmd_note)

    p_done = sub.add_parser("done", help="Mark item done")
    p_done.add_argument("id", type=int)
    p_done.set_defaults(func=cmd_done)

    p_list = sub.add_parser("list", help="List items")
    p_list.add_argument("--status", choices=STATUSES, default=None)
    p_list.set_defaults(func=cmd_list)

    p_render = sub.add_parser("render", help="Render ROADMAP.md")
    p_render.set_defaults(func=cmd_render)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
