"""
headless_ui.py — Headless drop-in replacement for ui.py's AetherUI.

Implements the exact public interface main.py's AetherLocal talks to
(set_state, write_log, confirm_blocking, update_message_stats,
show/hide_startup_panel, mark_startup_ready, set_startup_status,
wait_for_api_key, muted, current_file, on_text_command, on_reconfigure,
root.mainloop()) with no PyQt6 dependency and no GUI window.

Used when AETHER_HEADLESS is set (see main.py) — for running Aether on
a server with no display attached. Input in this mode comes from the
web dashboard / phone remote (dashboard/server.py already delivers
typed commands straight to AetherLocal._on_text_command, bypassing
ui.on_text_command entirely) or from stdin if AETHER_HEADLESS_STDIN=1.

Differences from the GUI version (by necessity — there's no one to
click a dialog):
  - wait_for_api_key() returns immediately. Headless deployments are
    expected to ship with config/api_keys.json already populated —
    there's no first-run setup wizard without a window to show it in.
  - confirm_blocking() routes through a real remote-approval flow: it
    registers the request with core.pending_approvals, fires an ntfy
    push (core.notify — requires "ntfy_topic" configured; no-ops
    otherwise) linking back to the dashboard, then blocks until
    POST /api/approvals/{id}/approve|deny resolves it or the timeout
    elapses. Still fails closed (denied) on timeout — there just may
    now be a human on the other end of the ntfy push to approve before
    that happens. Set AETHER_HEADLESS_AUTO_CONFIRM=1 to skip all of
    this and auto-approve instead — off by default, since this gates
    high-risk actions (shell exec, file delete, port scan, breach
    lookups; see core/safety.py).
  - current_file is always None — no drag-and-drop file panel exists
    headless. (Dashboard-based file upload, if added later, would set
    this the same way the GUI drop zone does today.)
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger("aether")


def _dashboard_url() -> str | None:
    """Best-effort dashboard URL for the ntfy "Click" link. Returns None
    if the dashboard module/deps aren't available -- send_ntfy() just
    omits the Click header in that case, not fatal either way. Uses
    dashboard.server's own IP/port/SSL helpers directly rather than
    instantiating a DashboardServer (which builds the whole FastAPI app
    in __init__ -- too heavy to spin up just for a URL string)."""
    try:
        from dashboard.server import DashboardServer, _local_ip, PORT
        proto = "https" if DashboardServer._ssl_enabled() else "http"
        return f"{proto}://{_local_ip()}:{PORT}"
    except Exception:
        return None


class _HeadlessRootShim:
    """Stands in for AetherUI.root — keeps the process alive the way
    ui.root.mainloop() (Qt's event loop) does, without pumping any GUI
    events, since there are none."""

    def mainloop(self) -> None:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[Aether] Shutting down (headless)...")

    def protocol(self, *_args) -> None:
        pass


class HeadlessUI:
    """Headless stand-in for ui.AetherUI. Constructor signature matches
    AetherUI("face.png") for drop-in compatibility; face_path/size are
    accepted and ignored — there's no window to skin."""

    def __init__(self, face_path: str = "face.png", size=None):
        self.muted          = False
        self.current_file   = None
        self.on_text_command = None
        self.on_reconfigure  = None
        self.on_stop_command = None
        self.chat_sessions: list = []
        self.root            = _HeadlessRootShim()
        self.state           = "IDLE"
        logger.info("[Aether] Running headless (no GUI) — AETHER_HEADLESS=1.")

    # ── State / logging ─────────────────────────────────────────────
    def set_chat_sessions(self, *args, **kwargs) -> None:
        """No chat picker headless; remember the list for introspection."""
        self.chat_sessions = list(args[0]) if args and isinstance(args[0], (list, tuple)) else []

    def clear_log(self) -> None:
        pass

    def set_state(self, state: str) -> None:
        self.state = state
        logger.debug(f"[state] {state}")

    def write_log(self, text: str) -> None:
        logger.info(text)

    # ── Confirmation gate (core.safety.SafetyLayer callback) ───────
    def confirm_blocking(self, message: str, timeout: float = 120.0) -> bool:
        auto = os.environ.get("AETHER_HEADLESS_AUTO_CONFIRM", "").strip().lower()
        if auto in ("1", "true", "yes", "allow"):
            logger.warning(
                f"[Confirm] {message} — AETHER_HEADLESS_AUTO_CONFIRM is set, "
                "auto-approving with no human review."
            )
            return True

        # Real remote-approval flow: register the request so the dashboard's
        # GET /api/approvals can show it with approve/deny buttons, push an
        # ntfy notification pointing back at the dashboard, then block until
        # POST /api/approvals/{id}/approve|deny resolves it -- or timeout,
        # which still fails closed (denied) same as before this existed.
        from core.pending_approvals import get_approval_store
        from core.notify import send_ntfy

        store = get_approval_store()
        approval = store.create(message, timeout=timeout)
        click_url = _dashboard_url()

        sent = send_ntfy(
            message=message,
            title="Aether needs your approval",
            click_url=click_url,
        )
        logger.warning(
            f"[Confirm] {message} — pending remote approval (id={approval.id}). "
            + ("ntfy push sent. " if sent else "ntfy push NOT sent (check ntfy_topic in config, or network). ")
            + f"Waiting up to {timeout:.0f}s for a response via the dashboard, "
            "denying if unanswered."
        )
        approved = store.wait(approval.id, timeout=timeout)
        logger.warning(f"[Confirm] {message} — {'approved' if approved else 'denied (no response or explicit deny)'}.")
        return approved

    # ── Message stats panel (no-op headless) ────────────────────────
    def update_message_stats(self, data: dict) -> None:
        logger.debug(f"[stats] {data}")

    # ── Startup panel (all no-op/log headless) ──────────────────────
    def show_startup_panel(self) -> None:
        logger.info("[startup] checking services…")

    def mark_startup_ready(self, key: str, error: bool = False) -> None:
        level = logger.error if error else logger.info
        level(f"[startup] {key}: {'error' if error else 'ready'}")

    def set_startup_status(self, text: str) -> None:
        logger.info(f"[startup] {text}")

    def hide_startup_panel(self) -> None:
        pass

    def wait_for_api_key(self) -> None:
        # No first-run dialog headless — config/api_keys.json is assumed
        # to already be populated. validate_config() downstream in
        # main.py's runner() still checks it and logs a clear error if not.
        return

    def start_speaking(self) -> None:
        self.set_state("SPEAKING")

    def stop_speaking(self) -> None:
        if not self.muted:
            self.set_state("LISTENING")
