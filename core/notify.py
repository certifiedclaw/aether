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
"""
from __future__ import annotations

import logging

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
