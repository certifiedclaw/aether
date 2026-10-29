"""
notify.py — Push notifications (ntfy.sh, or a self-hosted ntfy instance)
for events that need a human's attention while headless -- currently just
pending remote approvals (see core/pending_approvals.py, core/headless_ui.py).

NOTE: no ntfy integration existed anywhere in this codebase before this --
these are new config keys, not a reuse of something already wired up.
Configure via config/api_keys.json:
  "ntfy_base_url": "https://ntfy.sh"   (default; point at your own
                                         self-hosted instance if you run one)
  "ntfy_topic":    ""                  (required -- with no topic set,
                                         send_ntfy() is a silent no-op)
  "ntfy_remind_after_s": 60            (optional -- re-push once if an approval
                                         is still unanswered this long after
                                         the first push; 0 disables the reminder)

send_ntfy() itself is a single best-effort attempt. keep_notifying() is the
follow-through for approvals: it retries a failed first push with backoff and
sends one reminder if nobody has answered yet, stopping the moment the request
is resolved or its deadline passes.
"""
from __future__ import annotations

import logging
import threading
import time

import requests

from memory.config_manager import load_api_keys

logger = logging.getLogger("aether")


def send_ntfy(message: str, title: str | None = None, click_url: str | None = None,
              priority: str = "high", timeout: float = 5.0) -> bool:
    """Best-effort push via ntfy. Returns True only if the push was
    actually sent (HTTP < 300); False if not configured, or on any
    network/HTTP failure. Never raises -- a notification failure
    shouldn't block whatever's waiting on the thing being notified about."""
    cfg   = load_api_keys()
    base  = (cfg.get("ntfy_base_url") or "https://ntfy.sh").strip().rstrip("/")
    topic = (cfg.get("ntfy_topic") or "").strip()
    if not topic:
        logger.debug("[ntfy] no ntfy_topic configured in config/api_keys.json -- skipping push.")
        return False

    headers = {"Priority": priority}
    if title:
        headers["Title"] = title
    if click_url:
        headers["Click"] = click_url

    try:
        resp = requests.post(f"{base}/{topic}", data=message.encode("utf-8"),
                              headers=headers, timeout=timeout)
        ok = resp.status_code < 300
        if not ok:
            logger.warning(f"[ntfy] push failed: HTTP {resp.status_code}")
        return ok
    except Exception as e:
        logger.warning(f"[ntfy] push failed: {e}")
        return False


# Seconds to wait before each retry of a failed first push (3 retries total).
RETRY_DELAYS: tuple[float, ...] = (5.0, 15.0, 45.0)
DEFAULT_REMIND_AFTER_S = 60.0


def remind_after_seconds() -> float | None:
    """Configured reminder delay in seconds, or None if reminders are off."""
    raw = load_api_keys().get("ntfy_remind_after_s", DEFAULT_REMIND_AFTER_S)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_REMIND_AFTER_S
    return value if value > 0 else None


def keep_notifying(message: str, stop: threading.Event, deadline: float, *,
                   first_sent: bool, title: str | None = None,
                   click_url: str | None = None,
                   retry_delays: tuple[float, ...] = RETRY_DELAYS,
                   remind_after: float | None = None) -> int:
    """Follow-through for a push that someone must act on. Blocks, so run it
    on a daemon thread. Returns how many extra pushes were attempted.

    - If the first push failed (first_sent=False), retry after each delay in
      retry_delays until one succeeds or the delays run out.
    - Once a push has gone out, send ONE reminder after remind_after seconds if
      the request is still unresolved (and the reminder would land before the
      deadline).
    - Stops immediately when `stop` is set (request resolved) or when
      `deadline` (epoch seconds) passes. Never raises.

    The pending approval stays visible in the dashboard regardless; this only
    covers the operator not being told to look.
    """
    extra = 0
    try:
        sent = first_sent
        delays = iter(retry_delays)
        while not sent:
            delay = next(delays, None)
            if delay is None:
                logger.warning("[ntfy] gave up notifying after %d retries; the request is "
                               "still listed in the dashboard's Pending Approvals.", extra)
                return extra
            remaining = deadline - time.time()
            if remaining <= 0 or stop.wait(min(delay, remaining)) or time.time() >= deadline:
                return extra
            extra += 1
            sent = send_ntfy(message=message, title=title, click_url=click_url)
            if sent:
                logger.info("[ntfy] push delivered on retry %d.", extra)

        if remind_after is not None and remind_after > 0:
            if time.time() + remind_after >= deadline:
                return extra            # a reminder would arrive after it's moot
            if stop.wait(remind_after) or time.time() >= deadline:
                return extra
            extra += 1
            send_ntfy(message=f"Still waiting: {message}",
                      title=title or "Aether needs your approval", click_url=click_url)
    except Exception as e:  # noqa: BLE001 - a notification helper must never take anything down
        logger.warning(f"[ntfy] follow-up notifications stopped: {e}")
    return extra
