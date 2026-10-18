"""
core/goal_state.py — Aether Goal & State Manager

Addresses gap #3: "They remember conversations but not ongoing goals."

Inspired by AutoGPT's goal persistence and OpenHands' task state management.

What this does:
  - Goals survive session restarts (persisted to JSON)
  - Each goal has sub-tasks, status, context, and a priority
  - The agent can create/update/complete goals via tool calls
  - _build_system_prompt() injects active goals so the LLM is always aware
    of what it's working toward

Usage in main.py:
    from core.goal_state import GoalState
    self._goals = GoalState()
    self._goals.start()   # loads persisted goals

    # Inject into system prompt:
    goal_ctx = self._goals.format_for_prompt()

    # The LLM can call:
    self._execute_tool("goal_create", {"title": "...", "description": "..."})
    self._execute_tool("goal_update", {"goal_id": "abc123", "status": "in_progress"})
    self._execute_tool("goal_complete", {"goal_id": "abc123"})
    self._execute_tool("goal_list", {})
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class GoalStatus(str, Enum):
    PENDING     = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED     = "blocked"
    COMPLETED   = "completed"
    CANCELLED   = "cancelled"


class GoalPriority(str, Enum):
    LOW    = "low"
    NORMAL = "normal"
    HIGH   = "high"
    URGENT = "urgent"


@dataclass
class SubTask:
    task_id:     str
    description: str
    done:        bool = False
    result:      str  = ""


@dataclass
class Goal:
    goal_id:     str
    title:       str
    description: str
    status:      str  = GoalStatus.PENDING
    priority:    str  = GoalPriority.NORMAL
    created_at:  float = field(default_factory=time.time)
    updated_at:  float = field(default_factory=time.time)
    subtasks:    list[dict] = field(default_factory=list)
    context:     dict = field(default_factory=dict)
    notes:       str  = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Goal":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def is_active(self) -> bool:
        return self.status in (GoalStatus.PENDING, GoalStatus.IN_PROGRESS, GoalStatus.BLOCKED)

    def format_brief(self) -> str:
        icon = {
            GoalStatus.PENDING:     "⏳",
            GoalStatus.IN_PROGRESS: "🔄",
            GoalStatus.BLOCKED:     "🚧",
            GoalStatus.COMPLETED:   "✅",
            GoalStatus.CANCELLED:   "❌",
        }.get(self.status, "•")
        subtask_count = len(self.subtasks)
        done_count    = sum(1 for s in self.subtasks if s.get("done"))
        progress = f" [{done_count}/{subtask_count}]" if subtask_count else ""
        return f"{icon} [{self.priority}] {self.title}{progress}"


class GoalState:
    """
    Manages persistent goals across sessions.
    Goals are stored in the per-user data directory and reloaded on startup.
    """

    def __init__(self, config=None) -> None:
        try:
            from memory.config_manager import get_mark_config
            from core.data_home import resolve_data_path
            cfg = config or get_mark_config()
            self._path = resolve_data_path(cfg.get("goals.path"), "goals.json")
        except Exception:
            from core.data_home import data_path
            self._path = data_path("goals.json")

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._goals: dict[str, Goal] = {}
        self._lock  = threading.Lock()

    def start(self) -> None:
        """Load persisted goals. Call once at startup."""
        self._load()
        active = sum(1 for g in self._goals.values() if g.is_active())
        logger.info("GoalState: loaded %d goals (%d active)", len(self._goals), active)

    # ── CRUD ──────────────────────────────────────────────────────────────

    def create(self, title: str, description: str = "",
               priority: str = GoalPriority.NORMAL) -> Goal:
        goal = Goal(
            goal_id=str(uuid.uuid4())[:8],
            title=title,
            description=description,
            priority=priority,
        )
        with self._lock:
            self._goals[goal.goal_id] = goal
        self._save()
        logger.info("Goal created: [%s] %s", goal.goal_id, title)
        return goal

    def update(self, goal_id: str, **kwargs) -> str:
        with self._lock:
            g = self._goals.get(goal_id)
            if not g:
                return f"Goal {goal_id} not found."
            for k, v in kwargs.items():
                if hasattr(g, k):
                    setattr(g, k, v)
            g.updated_at = time.time()
        self._save()
        return f"Goal {goal_id} updated."

    def complete(self, goal_id: str, notes: str = "") -> str:
        return self.update(goal_id, status=GoalStatus.COMPLETED, notes=notes)

    def cancel(self, goal_id: str) -> str:
        return self.update(goal_id, status=GoalStatus.CANCELLED)

    def add_subtask(self, goal_id: str, description: str) -> str:
        with self._lock:
            g = self._goals.get(goal_id)
            if not g:
                return f"Goal {goal_id} not found."
            st = SubTask(task_id=str(uuid.uuid4())[:6], description=description)
            g.subtasks.append(asdict(st))
            g.updated_at = time.time()
        self._save()
        return f"Subtask added to goal {goal_id}."

    def complete_subtask(self, goal_id: str, task_id: str, result: str = "") -> str:
        with self._lock:
            g = self._goals.get(goal_id)
            if not g:
                return f"Goal {goal_id} not found."
            for st in g.subtasks:
                if st["task_id"] == task_id:
                    st["done"]   = True
                    st["result"] = result
                    break
            g.updated_at = time.time()
        self._save()
        return f"Subtask {task_id} completed."

    # ── Query ─────────────────────────────────────────────────────────────

    def get(self, goal_id: str) -> Goal | None:
        return self._goals.get(goal_id)

    def list_active(self) -> list[Goal]:
        with self._lock:
            return sorted(
                [g for g in self._goals.values() if g.is_active()],
                key=lambda g: (
                    {"urgent": 0, "high": 1, "normal": 2, "low": 3}.get(g.priority, 2),
                    g.created_at,
                )
            )

    def list_all(self) -> list[Goal]:
        with self._lock:
            return sorted(self._goals.values(), key=lambda g: g.created_at, reverse=True)

    # ── Prompt injection ──────────────────────────────────────────────────

    def format_for_prompt(self, max_goals: int = 5) -> str:
        """Returns a compact summary of active goals for injection into the system prompt."""
        active = self.list_active()[:max_goals]
        if not active:
            return ""
        lines = ["[Active Goals]"]
        for g in active:
            lines.append(f"  {g.format_brief()}")
            if g.description:
                lines.append(f"    → {g.description[:120]}")
        lines.append("[/Active Goals]")
        return "\n".join(lines)

    # ── Tool interface (called by _execute_tool in main.py) ───────────────

    def handle_tool(self, action: str, args: dict) -> str:
        """
        Dispatch goal tool calls from the LLM.
        action: goal_create | goal_update | goal_complete | goal_cancel |
                goal_list | goal_add_subtask
        """
        if action == "goal_create":
            g = self.create(
                title=args.get("title", "Untitled goal"),
                description=args.get("description", ""),
                priority=args.get("priority", GoalPriority.NORMAL),
            )
            return f"Goal created: [{g.goal_id}] {g.title}"

        if action == "goal_update":
            return self.update(args["goal_id"], **{k: v for k, v in args.items()
                                                    if k != "goal_id"})
        if action == "goal_complete":
            return self.complete(args["goal_id"], notes=args.get("notes", ""))

        if action == "goal_cancel":
            return self.cancel(args["goal_id"])

        if action == "goal_list":
            goals = self.list_active() if not args.get("all") else self.list_all()
            if not goals:
                return "No active goals."
            return "\n".join(g.format_brief() for g in goals)

        if action == "goal_add_subtask":
            return self.add_subtask(args["goal_id"], args["description"])

        return f"Unknown goal action: {action}"

    # ── Persistence ───────────────────────────────────────────────────────

    def _save(self) -> None:
        try:
            with self._lock:
                data = {gid: g.to_dict() for gid, g in self._goals.items()}
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug("GoalState save error: %s", e)

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            with self._lock:
                self._goals = {gid: Goal.from_dict(v) for gid, v in data.items()}
        except Exception as e:
            logger.warning("GoalState load error: %s", e)


# ── Goal tool declarations (add these to TOOL_DECLARATIONS in main.py) ────

GOAL_TOOL_DECLARATIONS = [
    {
        "name": "goal_manager",
        "description": (
            "Manages long-term goals that persist across sessions. "
            "Use to create, track, update, and complete ongoing objectives. "
            "ALWAYS use this when the user wants to track a multi-day project, "
            "recurring task, or ongoing goal."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "goal_create | goal_update | goal_complete | goal_cancel | goal_list | goal_add_subtask"
                },
                "goal_id":     {"type": "STRING",  "description": "Goal ID (for update/complete/cancel/subtask)"},
                "title":       {"type": "STRING",  "description": "Short goal title (for create)"},
                "description": {"type": "STRING",  "description": "Full goal description"},
                "priority":    {"type": "STRING",  "description": "low | normal | high | urgent"},
                "status":      {"type": "STRING",  "description": "pending | in_progress | blocked | completed"},
                "notes":       {"type": "STRING",  "description": "Completion notes"},
                "all":         {"type": "BOOLEAN", "description": "List all goals (including completed)"},
            },
            "required": ["action"]
        }
    }
]
