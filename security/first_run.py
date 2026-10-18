"""
security/first_run.py — first-time setup, run from Aether's desktop UI.

On Aether's very first launch (data/security/install.json does not exist),
this module:
  1. Generates a 16-char admin password
  2. Prints it once to the terminal
  3. Saves a SHA-256 hash + salt to data/security/install.json
  4. Issues a 6-char pairing PIN (valid 10 min)
  5. Generates a QR PNG at data/security/setup_qr.png that, when scanned by
     the phone, deep-links into the PWA / RN app's pairing wizard.

This module is **not** a network endpoint. It is invoked from main.py at
startup (or from a CLI helper). The phone learns the PIN by scanning the QR
that Aether's desktop UI displays — never via HTTP.

Public API:
    maybe_first_run()     — call from main.py; no-op if already installed
    print_first_run_banner()  — human-readable summary for the terminal
"""

from __future__ import annotations
import json
import secrets
import string
import socket
import time
from pathlib import Path

from core.data_home import data_path
from .tokens import get_manager
from . import audit


_QR_DIR = data_path("security")
_QR_PNG = _QR_DIR / "setup_qr.png"
_ADMIN_PASSWORD_LEN = 16


def _gen_admin_password() -> str:
    """Cryptographically random 16-char password, easy to read aloud."""
    # Avoid 0/O/1/l/I for readability
    alphabet = "".join(c for c in (string.ascii_letters + string.digits)
                       if c not in "0O1lI")
    return "".join(secrets.choice(alphabet) for _ in range(_ADMIN_PASSWORD_LEN))


def _local_ip() -> str:
    """Return the LAN IP of this machine. Duplicated from
    dashboard/server.py's _local_ip() to avoid an import cycle. If both
    modules are in the same process the answer is the same."""
    for probe in ("8.8.8.8", "1.1.1.1", "192.168.1.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect((probe, 80))
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith("127."):
                return ip
        except Exception:
            pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


def maybe_first_run() -> dict | None:
    """If install.json is missing, run the first-run flow. Returns the
    setup info dict (for the desktop UI to display) or None if already set up.

    The returned dict contains:
        admin_password      — cleartext, only returned ONCE
        pin                 — 6-char pairing PIN, valid 10 min
        qr_png_path         — path to a generated QR PNG (or None on failure)
        url                 — the URL the phone should open (e.g. http://192.168.1.20:8000)
    """
    mgr = get_manager()
    if mgr.is_installed():
        return None

    _QR_DIR.mkdir(parents=True, exist_ok=True)
    admin_password = _gen_admin_password()
    mgr.first_run_complete(admin_password)
    pin = mgr.issue_pin()

    # Build the QR target: the PWA's setup route, which auto-fills the PIN.
    # The PIN is in the URL fragment so it's not sent to the server in clear
    # via HTTP history; the server-side pending_pins.json still has to validate
    # it on submit.
    host = _local_ip()
    url = f"http://{host}:8000/pwa/#pin={pin}"

    qr_path: Path | None = None
    try:
        # Lazy import — qrcode is optional. If it's not installed, the user
        # just reads the PIN from the desktop UI banner.
        import qrcode  # type: ignore
        img = qrcode.make(url)
        img.save(str(_QR_PNG))
        qr_path = _QR_PNG
    except ImportError:
        qr_path = None
    except Exception:
        qr_path = None

    return {
        "admin_password": admin_password,
        "pin": pin,
        "qr_png_path": str(qr_path) if qr_path else None,
        "url": url,
        "issued_at": time.time(),
    }


def print_first_run_banner(info: dict) -> None:
    """Pretty-print the setup info to stdout. Called by main.py."""
    print("=" * 64)
    print("  AETHER — FIRST-RUN SETUP")
    print("=" * 64)
    print()
    print("  This appears only once. Save these values now.")
    print()
    print(f"  Admin password  :  {info['admin_password']}")
    print(f"  Pairing PIN     :  {info['pin']}")
    print(f"  Phone URL       :  {info['url']}")
    if info.get("qr_png_path"):
        print(f"  QR code         :  {info['qr_png_path']}")
    print()
    print("  Scan the QR code (or open the URL) on your phone to pair.")
    print("  The PIN expires in 10 minutes. Issue a new one from the")
    print("  Aether desktop UI if it expires.")
    print("=" * 64)


# ── CLI entry point: `python -m security.first_run` ──────────────────────
if __name__ == "__main__":
    info = maybe_first_run()
    if info is None:
        print("Aether is already set up. (data/security/install.json exists.)")
    else:
        print_first_run_banner(info)
