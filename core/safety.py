"""
safety.py — Aether v4.1 Safety & Verification Layer

Per-tool approval tiers now live in core/risk.py (enforced with @gated on each tool's
entry function); this module supplies the UI confirmation callback (confirm_action,
fail-closed) and the legacy path ACL. _UNCONDITIONAL_HIGH_RISK entries ("run_shell",
"shell", "rm", "rmdir") always require confirmation regardless of the confirm_on list
in config.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# FIX #1 — These tools are ALWAYS high-risk. They cannot be downgraded via
# config. If you add new destructive tools in the future, add them here too.
_UNCONDITIONAL_HIGH_RISK = frozenset({"run_shell", "shell", "rm", "rmdir"})

# These are high-risk by default but CAN be removed from confirm_on in config
# if the operator explicitly wants headless operation for them.
_DEFAULT_HIGH_RISK = frozenset({"delete", "write_file", "osint_port_scan", "osint_breach_check"})


@dataclass
class VerificationResult:
    success: bool
    message: str = ""
    details: dict = field(default_factory=dict)


class SafetyLayer:
    def __init__(self, config=None) -> None:
        from memory.config_manager import get_mark_config as get_config
        self._cfg = config or get_config()
        self._confirm_cb: Callable[[str], bool] | None = None

    def set_confirm_callback(self, cb: Callable[[str], bool]) -> None:
        self._confirm_cb = cb

    def is_path_allowed(self, path: str | Path) -> bool:
        try:
            target = Path(os.path.expanduser(str(path))).resolve()
            for allowed in self._cfg.allowed_paths:
                try:
                    target.relative_to(Path(allowed).resolve())
                    return True
                except ValueError:
                    continue
        except Exception:
            pass
        return False

    def requires_confirmation(self, operation: str) -> bool:
        op = operation.lower()
        # FIX #1 — unconditional high-risk tools always require confirmation,
        # independent of the config confirm_on list.
        if op in _UNCONDITIONAL_HIGH_RISK:
            return True
        return op in (set(self._cfg.confirm_on) | _DEFAULT_HIGH_RISK)

    def request_confirmation(self, operation: str, detail: str = "") -> bool:
        msg = f"Aether wants to perform: {operation}"
        if detail:
            msg += f"\n\n{detail}"
        if self._confirm_cb:
            return self._confirm_cb(msg)
        logger.warning("High-risk op '%s' denied (no UI callback registered)", operation)
        return False

    def verify_tool_result(self, tool: str, args: dict, result: str) -> VerificationResult:
        if not result or not result.strip():
            return VerificationResult(False, "Empty result")

        failure_phrases = [
            "error:", "traceback", "exception",
            "permission denied", "failed:", "no such file", "timed out",
        ]
        lower = result.lower()
        for phrase in failure_phrases:
            if phrase in lower:
                return VerificationResult(
                    False,
                    f"Failure phrase: '{phrase}'",
                    {"tool": tool},
                )
        return VerificationResult(True, "OK")


_safety: SafetyLayer | None = None


def get_safety(config=None) -> SafetyLayer:
    global _safety
    if _safety is None:
        _safety = SafetyLayer(config)
    return _safety


def confirm_action(operation: str, detail: str = "") -> bool:
    """Ask the USER (blocking UI dialog) to approve `operation`; True only on approval.

    Fail-closed by design: no UI callback registered, the dialog timing out, or any
    error at all means False. Use this inside the action that does the work (not only
    in the dispatcher) so every path that reaches it -- voice tools, the agent
    executor, plugins -- is covered."""
    try:
        return bool(get_safety().request_confirmation(operation, detail))
    except Exception as e:  # noqa: BLE001
        logging.getLogger("aether").warning(
            "confirmation unavailable, denying '%s': %s", operation, e)
        return False
