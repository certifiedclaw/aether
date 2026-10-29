"""Standalone dashboard: ``python -m dashboard``.

Runs only the FastAPI dashboard (approvals, LLM backend status, audit view)
without booting AetherLocal, the UI, STT/TTS or any model. Text commands sent
from a browser/phone have no assistant to run them, so they are drained and
dropped with a log line instead of piling up in memory.

Host/port/TLS behave exactly as in the full app (AETHER_DASHBOARD_HOST, certs).
"""
from __future__ import annotations

import asyncio
import queue
import sys
import threading


def _drain(dashboard, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            text = dashboard._command_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        print(f"[Dashboard] standalone mode: no assistant attached, dropped command ({len(str(text))} chars)")


def main() -> int:
    from dashboard import server

    if not server._DEPS_OK:
        print("[Dashboard] fastapi/uvicorn not installed.")
        print("[Dashboard] Run:  pip install fastapi 'uvicorn[standard]' cryptography")
        return 1

    dashboard = server.DashboardServer()
    stop = threading.Event()
    threading.Thread(target=_drain, args=(dashboard, stop), daemon=True,
                     name="DashboardDrain").start()
    try:
        asyncio.run(dashboard.serve())
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
