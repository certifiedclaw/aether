"""
core/undo.py — Aether Undo Stack

Aether could move, rename, overwrite, and create files with no way to take
a mis-heard command back — the only remedy was fixing it by hand. This adds
a real undo stack, wired into actions/file_controller.py, following the
same design constraints documented for FatihMakes/Mark-LIII's undo (see
https://github.com/FatihMakes/Mark-LIII):

  - It does not guess. Where the previous state can't be captured cheaply
    and safely (a file too large to buffer, a binary file, a delete — see
    below), no undo entry is registered at all, rather than registering
    one that might restore the wrong thing.
  - It does not hoard. Buffered "previous content" for an overwritten file
    is capped at UNDO_MAX_BYTES; larger files are skipped rather than held
    in memory for the rest of the session.
  - It costs nothing at rest. register() just appends a closure; nothing
    in it runs unless undo_last() is called.

Deliberately NOT covering delete_file(): actions/file_controller.py already
routes every delete through send2trash (see _safe_trash), so the OS
Recycle Bin/Trash is the existing recovery path for that one operation —
unlike a bespoke undo entry, it doesn't need file content buffered in
memory, and it survives a restart. Duplicating that as an in-memory undo
entry would be strictly worse: two recovery paths that can disagree.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

# Matches the cutoff Mark-LIII documents for its own undo: buffering a
# file's previous content for undo is cheap for ordinary text/code files
# and wasteful (or, for a large binary, actually risky memory-wise) past
# this size.
UNDO_MAX_BYTES = 1_000_000  # 1 MB

_MAX_HISTORY = 20  # oldest entries drop off rather than growing unbounded


@dataclass
class _Entry:
    description: str
    _undo: Callable[[], str]


class UndoStack:
    """Thread-safe. One process-wide instance via get_undo_stack()."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stack: list[_Entry] = []

    def register(self, description: str, undo_fn: Callable[[], str]) -> None:
        with self._lock:
            self._stack.append(_Entry(description, undo_fn))
            if len(self._stack) > _MAX_HISTORY:
                self._stack.pop(0)

    def history(self) -> list[str]:
        """Most recent first — what the `undo` tool's action='list' shows."""
        with self._lock:
            return [e.description for e in reversed(self._stack)]

    def undo_last(self) -> str:
        with self._lock:
            if not self._stack:
                return "There's nothing for me to undo."
            entry = self._stack.pop()
        try:
            result = entry._undo()
            return result or f"Undone: {entry.description}"
        except Exception as e:
            return f"Could not undo '{entry.description}': {e}"


_stack: UndoStack | None = None
_stack_lock = threading.Lock()


def get_undo_stack() -> UndoStack:
    global _stack
    if _stack is None:
        with _stack_lock:
            if _stack is None:
                _stack = UndoStack()
    return _stack


# Module-level convenience wrappers — mirrors core/pending_approvals.py's
# get_approval_store() pattern already used elsewhere in this codebase.
def register(description: str, undo_fn: Callable[[], str]) -> None:
    get_undo_stack().register(description, undo_fn)


def undo_last() -> str:
    return get_undo_stack().undo_last()


def history() -> list[str]:
    return get_undo_stack().history()
