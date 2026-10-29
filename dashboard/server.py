"""
dashboard/server.py — AETHER Local Dashboard

The dashboard binds to loopback by default. Remote access requires an explicit
AETHER_DASHBOARD_HOST and TLS certificates in config/certs/aether.{key,crt}.

Install deps:  pip install fastapi "uvicorn[standard]" cryptography
"""

import asyncio
import queue as _queue
import base64
import hashlib
import os
import re
import secrets
import socket
import string
import time
from pathlib import Path

_DEPS_OK = False
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
    import uvicorn
    _DEPS_OK = True
except ImportError:
    pass

# python-multipart is required for file uploads — optional dependency.
# NOTE: importing UploadFile/File from fastapi always succeeds (they're just
# fastapi's own classes) regardless of whether python-multipart is actually
# installed -- the real dependency only gets exercised later, when FastAPI
# builds the request-parsing "dependant" for a route that uses UploadFile/
# File(...) as a parameter (see _build_app()'s /api/upload route below).
# That happens at *route-registration* time, not import time, and raises
# RuntimeError if python-multipart is missing -- so checking only the
# fastapi import here would make _UPLOAD_OK always True and crash the whole
# dashboard (not just uploads) on any install missing python-multipart.
# Use fastapi's own internal availability check so this stays correct
# regardless of which of python-multipart's two import names is installed.
_UPLOAD_OK = False
try:
    from fastapi import UploadFile, File as FastAPIFile
    from fastapi.dependencies.utils import ensure_multipart_is_installed
    ensure_multipart_is_installed()
    _UPLOAD_OK = True
except Exception:
    pass

BASE_DIR    = Path(__file__).resolve().parent.parent
STATIC_DIR  = Path(__file__).parent / "static"
PORT        = 8000
MAX_UPLOAD_MB = 500
WS_TICKET_TTL = 30
MAX_WS_TICKETS = 1000


def _make_uploads_dir() -> Path:
    """Return (and create) the cross-platform uploads folder."""
    for candidate in [
        Path.home() / "Downloads" / "AETHER Uploads",
        Path.home() / "Documents" / "AETHER Uploads",
        BASE_DIR / "uploads",
    ]:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except Exception:
            pass
    return BASE_DIR / "uploads"


UPLOADS_DIR = _make_uploads_dir()

# NOTE: Aether uses a local Ollama LLM + Whisper STT, not Gemini. The
# _get_gemini_key() helper from the upstream project was dead code (never
# called) and has been removed rather than ported over unused.

_KEY_CHARS = [c for c in (string.ascii_uppercase + string.digits)
              if c not in ('O', 'I', 'L', '0', '1')]

# ── AES-256-CBC ───────────────────────────────────────────────────────────────
_AES_SALT = b'AETHER-DASHBOARD-v1'


def _derive_key(session_key: str) -> bytes:
    """SHA-256(sessionKey‖salt) → 32-byte AES-256 key (microseconds, no PBKDF2 needed)."""
    return hashlib.sha256(session_key.encode('utf-8') + _AES_SALT).digest()


def _new_session_key() -> str:
    """Random 256-bit per-session key. The 6-char pairing code only authorises a login;
    it is never used as key material (it has ~29 bits, so it could be brute-forced
    offline from a captured message)."""
    return secrets.token_urlsafe(32)


def _derive_mac_key(session_key: str) -> bytes:
    """Independent HMAC-SHA256 key (domain-separated from the AES key)."""
    return hashlib.sha256(b'MAC|' + session_key.encode('utf-8') + _AES_SALT).digest()


def _decrypt_cbc(aes_key: bytes, enc_b64: str, mac_key: bytes) -> str:
    """Verify-then-decrypt base64(IV[16] || ciphertext || HMAC[32]).

    The MAC covers IV||ciphertext and is checked (constant time) BEFORE any decryption,
    so tampered or padding-oracle probes are rejected. mac_key is required."""
    import hmac
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sym_pad
    if not mac_key:
        raise ValueError("mac_key required")
    raw = base64.b64decode(enc_b64, validate=True)
    if len(raw) < 16 + 16 + 32 or (len(raw) - 48) % 16:
        raise ValueError("bad length")
    body, tag = raw[:-32], raw[-32:]
    if not hmac.compare_digest(hmac.new(mac_key, body, hashlib.sha256).digest(), tag):
        raise ValueError("bad MAC")
    iv, ct   = body[:16], body[16:]
    dec      = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
    padded   = dec.update(ct) + dec.finalize()
    unpadder = sym_pad.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode('utf-8')


# ── login brute-force limiter ─────────────────────────────────────────────────
_MAX_FAILS   = 5
_LOCKOUT_S   = 900.0     # 15 min
_FAIL_WINDOW = 600.0     # 10 min


class _LoginLimiter:
    """5 bad pairing codes from one IP in 10 min -> locked out for 15 min."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._fails: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}

    def locked(self, ip: str) -> float:
        """Seconds of lockout remaining (0 = allowed)."""
        left = self._locked.get(ip, 0.0) - self._clock()
        if left <= 0:
            self._locked.pop(ip, None)
            return 0.0
        return left

    def fail(self, ip: str) -> None:
        now = self._clock()
        recent = [t for t in self._fails.get(ip, []) if now - t < _FAIL_WINDOW] + [now]
        self._fails[ip] = recent
        if len(recent) >= _MAX_FAILS:
            self._locked[ip] = now + _LOCKOUT_S
            self._fails.pop(ip, None)
        if len(self._fails) > 1000:          # bound memory
            self._fails.clear()

    def ok(self, ip: str) -> None:
        self._fails.pop(ip, None)


# ── CryptoJS (auto-download once, served locally) ─────────────────────────────
_CRYPTOJS_CDN  = ("https://cdnjs.cloudflare.com/ajax/libs/"
                  "crypto-js/4.2.0/crypto-js.min.js")
_CRYPTOJS_FILE = STATIC_DIR / "crypto-js.min.js"


def _ensure_network_access(port: int) -> None:
    """Cross-platform, best-effort: open port in the OS firewall for LAN access.

    Runs in a background thread — never blocks uvicorn startup.

    Windows : writes a .bat file, runs it elevated via Windows ShellExecuteW
              (native UAC dialog, guaranteed to appear). One-time setup.
    macOS   : osascript admin dialog if the Application Firewall is on.
    Linux   : pkexec GUI → sudo -n → prints manual command as fallback.
    """
    import sys

    if sys.platform == "win32":
        _ensure_network_access_windows(port)
    elif sys.platform == "darwin":
        _ensure_network_access_macos(port)
    else:
        _ensure_network_access_linux(port)


def _ensure_network_access_windows(port: int) -> None:
    import sys, subprocess, os, tempfile, threading
    import ctypes, time

    port_rule = f"AETHER Dashboard Port {port}"
    prog_rule  = "AETHER Dashboard Python"
    py_exe     = sys.executable

    def _netsh_rule_exists(name: str) -> bool:
        try:
            r = subprocess.run(
                ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
                capture_output=True, text=True, timeout=5,
            )
            return r.returncode == 0 and "No rules match" not in r.stdout
        except Exception:
            return False

    def _network_is_public() -> bool:
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "(Get-NetConnectionProfile | "
                 "Where-Object {$_.NetworkCategory -eq 'Public'} | "
                 "Measure-Object).Count"],
                capture_output=True, text=True, timeout=6,
            )
            return r.stdout.strip() not in ("", "0")
        except Exception:
            return False

    need_port    = not _netsh_rule_exists(port_rule)
    need_prog    = not _netsh_rule_exists(prog_rule)
    need_private = _network_is_public()

    if not need_port and not need_prog and not need_private:
        return  # already fully configured

    # Build a .bat file — netsh + powershell, runs fast when elevated
    bat_lines = ["@echo off"]
    if need_private:
        bat_lines.append(
            'powershell -NoProfile -NonInteractive -Command "'
            'Get-NetConnectionProfile | '
            "Where-Object {$_.NetworkCategory -eq 'Public'} | "
            'Set-NetConnectionProfile -NetworkCategory Private"'
        )
    if need_port:
        bat_lines.append(
            f'netsh advfirewall firewall add rule '
            f'name="{port_rule}" protocol=TCP dir=in '
            f'localport={port} action=allow'
        )
    if need_prog:
        bat_lines.append(
            f'netsh advfirewall firewall add rule '
            f'name="{prog_rule}" dir=in action=allow '
            f'program="{py_exe}" enable=yes'
        )

    bat_body = "\r\n".join(bat_lines) + "\r\n"
    fd, bat_path = tempfile.mkstemp(suffix=".bat", prefix="aether_fw_")
    try:
        os.write(fd, bat_body.encode("mbcs"))   # Windows cmd.exe expects ANSI
        os.close(fd)
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        return

    # ── Try running directly (succeeds when already admin) ────────────────
    try:
        # No shell=True: a .bat runs directly via CreateProcess, and routing the
        # temp path through cmd.exe would let a '&' or '^' in the user's temp
        # directory name be read as a command separator.
        r = subprocess.run(
            [bat_path], capture_output=True, timeout=8
        )
        if r.returncode == 0:
            print(f"[Dashboard] Firewall configured for port {port}.")
            try:
                os.unlink(bat_path)
            except Exception:
                pass
            return
    except Exception:
        pass

    # ── ShellExecuteW: native UAC elevation (most reliable on Windows) ────
    # ShellExecuteW with verb "runas" always shows the UAC dialog regardless
    # of UAC level settings. Non-blocking — uvicorn is already running.
    print("[Dashboard] One-time network setup required.")
    print("[Dashboard] >>> A Windows security dialog will appear — click 'Yes' <<<")
    try:
        ret = ctypes.windll.shell32.ShellExecuteW(
            None,       # hwnd  (no parent window)
            "runas",    # verb  (request elevation)
            bat_path,   # file  (our .bat)
            None,       # params
            None,       # working dir
            0,          # SW_HIDE (run without a visible cmd window)
        )
        if int(ret) > 32:
            # ShellExecuteW returns immediately; bat finishes in ~1 second.
            # Sleep briefly so the rules are in place before the first retry.
            time.sleep(2)
            print(f"[Dashboard] Network setup complete — port {port} is open.")
            print("[Dashboard] Refresh your phone browser to connect.")
        else:
            print("[Dashboard] Setup was not allowed.")
            print("[Dashboard] Phone connections may fail until AETHER is run as Administrator.")
    except Exception as e:
        print(f"[Dashboard] Firewall setup error: {e}")
    finally:
        # Cleanup after the bat has had time to run
        def _cleanup(path: str) -> None:
            time.sleep(5)
            try:
                os.unlink(path)
            except Exception:
                pass
        threading.Thread(target=_cleanup, args=(bat_path,), daemon=True).start()


def _ensure_network_access_macos(port: int) -> None:
    import subprocess

    fw_ctl = "/usr/libexec/ApplicationFirewall/socketfilterfw"
    try:
        r = subprocess.run(
            [fw_ctl, "--getglobalstate"], capture_output=True, text=True, timeout=5,
        )
        if "disabled" in r.stdout.lower():
            return  # firewall off — nothing to do

        import sys
        py = sys.executable
        listed = subprocess.run(
            [fw_ctl, "--listapps"], capture_output=True, text=True, timeout=5,
        )
        if py in listed.stdout:
            return  # already allowed

        print("[Dashboard] One-time network setup — enter your password in the macOS dialog.")
        subprocess.run(
            ["osascript", "-e",
             f'do shell script "{fw_ctl} --add {py} && {fw_ctl} --unblockapp {py}"'
             f' with administrator privileges'],
            timeout=60,
        )
    except Exception:
        pass  # macOS firewall is off by default — silent failure is fine


def _ensure_network_access_linux(port: int) -> None:
    import subprocess

    def _privileged(cmd: list[str]) -> bool:
        for prefix in (["pkexec"], ["sudo", "-n"]):
            try:
                r = subprocess.run(prefix + cmd, capture_output=True, timeout=30)
                if r.returncode == 0:
                    return True
            except Exception:
                pass
        return False

    try:  # ufw
        r = subprocess.run(["ufw", "status"], capture_output=True, text=True, timeout=5)
        if "active" in r.stdout.lower():
            if _privileged(["ufw", "allow", f"{port}/tcp"]):
                print(f"[Dashboard] ufw: port {port} allowed.")
            else:
                print(f"[Dashboard] Run manually:  sudo ufw allow {port}/tcp")
            return
    except FileNotFoundError:
        pass

    try:  # firewalld
        r = subprocess.run(
            ["firewall-cmd", "--state"], capture_output=True, text=True, timeout=5,
        )
        if "running" in r.stdout.lower():
            ok = (_privileged(["firewall-cmd", "--add-port", f"{port}/tcp", "--permanent"])
                  and _privileged(["firewall-cmd", "--reload"]))
            if ok:
                print(f"[Dashboard] firewalld: port {port} allowed.")
            else:
                print(f"[Dashboard] Run manually:  sudo firewall-cmd --add-port={port}/tcp --permanent && sudo firewall-cmd --reload")
            return
    except FileNotFoundError:
        pass

    try:  # iptables (not persistent but works until reboot)
        r = subprocess.run(["iptables", "-L", "INPUT", "-n"], capture_output=True, timeout=5)
        if r.returncode == 0:
            if _privileged(["iptables", "-A", "INPUT", "-p", "tcp", "--dport", str(port), "-j", "ACCEPT"]):
                print(f"[Dashboard] iptables: port {port} opened.")
            else:
                print(f"[Dashboard] Run manually:  sudo iptables -A INPUT -p tcp --dport {port} -j ACCEPT")
    except FileNotFoundError:
        pass  # no iptables means firewall is probably off — nothing to do


def _ensure_crypto_js() -> None:
    if _CRYPTOJS_FILE.exists():
        return
    try:
        import urllib.request
        print("[Dashboard] Downloading CryptoJS (one-time setup)…")
        urllib.request.urlretrieve(_CRYPTOJS_CDN, str(_CRYPTOJS_FILE))
        print("[Dashboard] CryptoJS cached — will serve locally from now on.")
    except Exception as e:
        print(f"[Dashboard] CryptoJS download failed: {e}")
        print(f"[Dashboard] Encryption will fall back to CDN load on client.")


_ensure_crypto_js()


# ── helpers ───────────────────────────────────────────────────────────────────

def _local_ip() -> str:
    """Return the best LAN-facing IPv4 address, no internet required."""
    # Method 1: route trick (fast, works when internet is available)
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

    # Method 2: hostname resolution (works offline on most systems)
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if not ip.startswith("127."):
            return ip
    except Exception:
        pass

    # Method 3: enumerate all interfaces (fully offline, no external deps)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    except Exception:
        pass

    return "127.0.0.1"


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


# ── DashboardServer ───────────────────────────────────────────────────────────

class DashboardServer:

    def __init__(self):
        self._host                        = os.environ.get("AETHER_DASHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1"
        self._ip                          = self._advertised_ip()
        self._tokens: set[str]            = set()
        self._token_keys: dict[str, str]  = {}   # auth_token → session_key
        self._ws_tickets: dict[str, tuple[str, str, float]] = {}
        self._aes_cache:  dict[str, tuple]= {}   # session_key → (AES key, MAC key)
        self._limiter = _LoginLimiter()
        self._clients: set[WebSocket]     = set()
        self._history: list[dict]         = []
        # Thread-safe (not asyncio.Queue) so Aether's threaded main.py can
        # poll it directly with queue.get(timeout=...) from a normal thread.
        self._command_queue               = _queue.Queue()
        self._wake_callback               = None
        self._connect_callback            = None
        self._pending_keys: dict[str, float] = {}
        self._device_sessions: dict[str, dict] = {}  # device_token → {session_key}
        self._phone_audio_queue: _queue.Queue     = _queue.Queue(maxsize=200)
        self._uploads_dir                 = UPLOADS_DIR
        self._login_html                  = _read("login.html")
        self._app_html                    = _read("app.html")
        self.app                          = self._build_app()

    @staticmethod
    def _is_loopback_host(host: str) -> bool:
        return host.strip().lower() in {"localhost", "127.0.0.1", "::1"}

    @classmethod
    def _remote_access_allowed(cls, host: str, ssl_enabled: bool) -> bool:
        return cls._is_loopback_host(host) or ssl_enabled

    def _advertised_ip(self) -> str:
        if self._is_loopback_host(self._host):
            return "127.0.0.1"
        if self._host not in {"0.0.0.0", "::"}:
            return self._host
        return _local_ip()

    # ── one-time key management ───────────────────────────────────────────

    def new_key(self, expiry_secs: int = 600) -> str:
        now = time.time()
        self._pending_keys = {k: v for k, v in self._pending_keys.items() if v > now}
        key = ''.join(secrets.choice(_KEY_CHARS) for _ in range(6))
        self._pending_keys[key] = now + expiry_secs
        return key

    @staticmethod
    def _ssl_enabled() -> bool:
        from core import config_home as _ch
        certs = _ch.certs_dir()
        return (certs / "aether.key").exists() and (certs / "aether.crt").exists()

    def pairing_info(self, expiry_secs: int = 600) -> dict:
        """Everything the user needs to connect a browser or phone: the address, a one-time
        6-character code, and a QR/auto-login link. Nothing else in the app creates a code,
        so without this the login page ("press Remote Control...") could never be passed."""
        code = self.new_key(expiry_secs)
        base = self.get_url()
        return {
            "url": base,
            "manual_url": self.get_manual_url(),
            "code": code,
            "auto_login_url": f"{base}/auto-login?key={code}",
            "https": self._tls_active(),
            "loopback": self._is_loopback_host(self._host),
            "expires_min": max(1, expiry_secs // 60),
        }

    def _tls_active(self) -> bool:
        """TLS is used when a certificate exists AND the dashboard is reachable from other
        machines. On loopback it only adds a certificate warning (and makes a plain
        http://localhost:8000 fail with an empty response), so loopback stays plain HTTP."""
        return self._ssl_enabled() and not self._is_loopback_host(self._host)

    def get_url(self) -> str:
        proto = "https" if self._tls_active() else "http"
        return f"{proto}://{self._ip}:{PORT}"

    def get_manual_url(self) -> str:
        """URL for manual browser entry. When HTTPS active, points to alias port (also HTTPS)."""
        if self._tls_active():
            return f"{self._ip}:{PORT + 1}"
        return f"{self._ip}:{PORT}"

    def _keys(self, session_key: str) -> tuple:
        if session_key not in self._aes_cache:
            self._aes_cache[session_key] = (_derive_key(session_key),
                                            _derive_mac_key(session_key))
        return self._aes_cache[session_key]

    def _decrypt(self, token: str, enc_b64: str) -> str | None:
        sk = self._token_keys.get(token)
        if not sk:
            return None
        try:
            aes, mac = self._keys(sk)
            return _decrypt_cbc(aes, enc_b64, mac)
        except Exception:
            return None

    # ── callbacks ────────────────────────────────────────────────────────

    def set_wake_callback(self, fn) -> None:
        self._wake_callback = fn

    def set_connect_callback(self, fn) -> None:
        self._connect_callback = fn

    # ── broadcast ────────────────────────────────────────────────────────

    async def broadcast(self, msg: dict) -> None:
        self._history.append(msg)
        if len(self._history) > 300:
            self._history = self._history[-300:]
        dead: set[WebSocket] = set()
        for ws in list(self._clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    # ── FastAPI app ───────────────────────────────────────────────────────

    def _auth(self, req: Request) -> bool:
        tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
        return bool(tok) and tok in self._tokens

    def _issue_ws_ticket(self, token: str, purpose: str) -> str | None:
        if (token not in self._tokens or not isinstance(purpose, str)
                or purpose not in {"main", "phone-audio"}):
            return None
        now = time.monotonic()
        self._ws_tickets = {
            key: value for key, value in self._ws_tickets.items() if value[2] > now
        }
        while len(self._ws_tickets) >= MAX_WS_TICKETS:
            oldest = min(self._ws_tickets, key=lambda key: self._ws_tickets[key][2])
            del self._ws_tickets[oldest]
        ticket = secrets.token_urlsafe(24)
        self._ws_tickets[ticket] = (token, purpose, now + WS_TICKET_TTL)
        return ticket

    def _consume_ws_ticket(self, ticket: str, purpose: str) -> str | None:
        item = self._ws_tickets.pop(ticket, None)
        if not item:
            return None
        token, expected_purpose, expires = item
        if expires <= time.monotonic() or expected_purpose != purpose or token not in self._tokens:
            return None
        return token

    @staticmethod
    def _safe_filename(raw: str) -> str:
        name = Path(raw).name                          # strip path components
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(". ")
        return name or "upload"

    def _build_app(self) -> "FastAPI":
        app = FastAPI(docs_url=None, redoc_url=None)

        # Opt-in Stage-2 security router (security/, dashboard/api_secure.py).
        # Fully separate surface under /api/secure/*, own bearer-token
        # sessions -- does not touch _auth/_tokens below. Off by default;
        # requires explicit env var so existing installs aren't affected
        # until the user has actually gone through security/first_run.py.
        if os.environ.get("AETHER_SECURE_ROUTER") == "1":
            try:
                from .api_secure import router as security_router
                app.include_router(security_router, prefix="/api/secure")
            except Exception as e:
                print(f"[Aether] secure router not mounted: {e}")

        self._mount_openai_api(app)
        self._register_static_routes(app)
        self._register_auth_routes(app)
        self._register_command_routes(app)
        self._register_approval_routes(app)
        self._register_llm_status_routes(app)
        self._register_audit_routes(app)
        self._register_perf_routes(app)
        self._register_phone_audio_ws(app)
        self._register_file_routes(app)
        self._register_main_ws(app)

        return app

    def _mount_openai_api(self, app: "FastAPI") -> None:
        """OpenAI-compatible /v1 routes (dashboard/openai_api.py). Routes are always
        registered but answer 404 until api_server_enabled + api_server_token are set."""
        try:
            from core import llm_client as _llm
            from .openai_api import build_router
            app.include_router(build_router(
                lambda: _llm._load_config(),
                lambda *a, **k: _llm.call_llm_stream(*a, **k),
                lambda: _llm.get_llm_settings()[1]))
        except Exception as e:
            print(f"[Aether] OpenAI-compatible API not mounted: {e}")

    # ── static / login page routes ────────────────────────────────────────

    def _register_static_routes(self, app: "FastAPI") -> None:
        # serve CryptoJS from local cache, fallback to CDN redirect
        @app.get("/static/crypto.js")
        async def serve_crypto():
            if _CRYPTOJS_FILE.exists():
                return FileResponse(str(_CRYPTOJS_FILE),
                                    media_type="application/javascript")
            from fastapi.responses import RedirectResponse
            return RedirectResponse(_CRYPTOJS_CDN)

        @app.get("/login", response_class=HTMLResponse)
        async def login_page():
            return HTMLResponse(self._login_html)

        @app.get("/", response_class=HTMLResponse)
        async def index():
            # Auth is handled client-side via sessionStorage bearer token.
            # Server-side header auth can't work here because browser navigations
            # don't send custom headers (location.href doesn't carry Authorization).
            html = (self._app_html
                    .replace("__IP__", self._ip)
                    .replace("__PORT__", str(PORT)))
            return HTMLResponse(html)

    # ── auth / pairing routes ────────────────────────────────────────────

    def _register_auth_routes(self, app: "FastAPI") -> None:
        @app.post("/api/ws-ticket")
        async def create_ws_ticket(req: Request):
            if not self._auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                body = await req.json()
            except Exception:
                body = {}
            purpose = body.get("purpose") if isinstance(body, dict) else None
            token = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            ticket = self._issue_ws_ticket(token, purpose)
            if not ticket:
                return JSONResponse({"error": "Invalid WebSocket purpose"}, status_code=400)
            return JSONResponse({"ticket": ticket}, headers={"Cache-Control": "no-store"})

        @app.post("/login")
        async def login(req: Request):
            ip = req.client.host if req.client else "?"
            wait = self._limiter.locked(ip)
            if wait > 0:
                return JSONResponse({"ok": False, "error": "Too many attempts. Try again later."},
                                    status_code=429, headers={"Retry-After": str(int(wait) + 1)})
            try:
                body = await req.json()
            except Exception:
                body = {}
            entered = str(body.get("pin", "")).strip().upper() if isinstance(body, dict) else ""
            now     = time.time()
            if entered in self._pending_keys and self._pending_keys[entered] > now:
                del self._pending_keys[entered]          # one-time use
                self._limiter.ok(ip)
                skey = _new_session_key()                # random; NOT the pairing code
                tok = secrets.token_urlsafe(32)
                self._tokens.add(tok)
                self._token_keys[tok] = skey
                self._keys(skey)                         # pre-derive & cache
                if self._connect_callback:
                    self._connect_callback()
                asyncio.create_task(self.broadcast(
                    {"type": "sys", "text": "Remote connection established."}
                ))
                # Bearer token in response body — no cookies needed (works on any browser/HTTP)
                return JSONResponse({"ok": True, "token": tok, "key": skey})
            self._limiter.fail(ip)
            return JSONResponse({"ok": False, "error": "Invalid or expired key"},
                                status_code=401)

        @app.get("/auto-login")
        async def auto_login(req: Request, key: str = ""):
            """QR code target — validates one-time key, creates session, redirects phone."""
            now = time.time()
            ip = req.client.host if req.client else "?"
            if self._limiter.locked(ip):
                return HTMLResponse("Too many attempts.", status_code=429)
            if not key or key not in self._pending_keys or self._pending_keys[key] <= now:
                self._limiter.fail(ip)
                return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<style>
  body{background:#07090f;color:#dde3ed;font-family:sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}
  h2{color:#f87171;margin-bottom:12px}p{color:#5e6a7e;font-size:14px}
</style></head>
<body><div><h2>Link Expired</h2>
<p>Press <strong style="color:#dde3ed">Remote Control</strong> in AETHER to get a new QR code.</p>
</div></body></html>""")

            del self._pending_keys[key]
            self._limiter.ok(ip)
            skey    = _new_session_key()
            tok     = secrets.token_urlsafe(32)
            dev_tok = secrets.token_urlsafe(32)
            self._tokens.add(tok)
            self._token_keys[tok] = skey
            self._keys(skey)
            self._device_sessions[dev_tok] = {"session_key": skey}

            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Remote connection established via QR code."}
            ))

            return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<style>
  body{{background:#07090f;color:#dde3ed;font-family:sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}}
  p{{color:#5e6a7e;font-size:14px}}
</style></head>
<body>
<script>
  sessionStorage.setItem('aether_token','{tok}');
  sessionStorage.setItem('aether_key','{skey}');
  localStorage.setItem('aether_device_token','{dev_tok}');
  setTimeout(function(){{location.replace('/')}},400);
</script>
<p>Connecting to AETHER…</p>
</body></html>""")

        @app.post("/api/device-login")
        async def device_login_ep(req: Request):
            """Return a fresh auth token for a previously paired device token."""
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"ok": False}, status_code=400)
            dev_tok = (body.get("device_token") or "").strip()
            if not dev_tok or dev_tok not in self._device_sessions:
                return JSONResponse({"ok": False}, status_code=401)
            session_key = self._device_sessions[dev_tok]["session_key"]
            tok = secrets.token_urlsafe(32)
            self._tokens.add(tok)
            self._token_keys[tok] = session_key
            self._keys(session_key)
            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Known device reconnected automatically."}
            ))
            return JSONResponse({"ok": True, "token": tok, "key": session_key})

        @app.post("/api/revoke-devices")
        async def revoke_devices(req: Request):
            """Invalidate all persistent device tokens (admin action)."""
            if not self._auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            count = len(self._device_sessions)
            self._device_sessions.clear()
            return JSONResponse({"ok": True, "revoked": count})

    # ── pending approvals (headless remote-approval flow) ──────────────────

    def _register_approval_routes(self, app: "FastAPI") -> None:
        """Backs core.headless_ui.HeadlessUI.confirm_blocking()'s remote-
        approval flow -- see core/pending_approvals.py. Listing and
        resolving both require the same session auth as everything else
        here; there's no separate approval-specific auth, on the
        assumption that anyone who can reach the authenticated dashboard
        is the operator this whole flow exists to reach."""

        @app.get("/api/approvals")
        async def list_approvals(req: Request):
            if not self._auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            from core.pending_approvals import get_approval_store
            return JSONResponse({"approvals": get_approval_store().list_pending()})

        @app.post("/api/approvals/{approval_id}/approve")
        async def approve_approval(approval_id: str, req: Request):
            if not self._auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            from core.pending_approvals import get_approval_store
            if not get_approval_store().resolve(approval_id, True):
                return JSONResponse({"error": "Not found or already resolved"}, status_code=404)
            return JSONResponse({"ok": True})

        @app.post("/api/approvals/{approval_id}/deny")
        async def deny_approval(approval_id: str, req: Request):
            if not self._auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            from core.pending_approvals import get_approval_store
            if not get_approval_store().resolve(approval_id, False):
                return JSONResponse({"error": "Not found or already resolved"}, status_code=404)
            return JSONResponse({"ok": True})

    # ── LLM backend status (remote-PC / local-CPU fallback) ────────────────

    def _register_llm_status_routes(self, app: "FastAPI") -> None:
        """Web-dashboard counterpart of the "LLM Backend" line in the CLI
        diagnostics/"status" command -- see core.llm_client.get_llm_backend_status()."""

        @app.get("/api/llm-status")
        async def llm_status_ep(req: Request):
            if not self._auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            from core.llm_client import get_llm_backend_status
            # In dual mode this can probe the remote PC with a timeout: never on the event loop.
            return JSONResponse(await asyncio.get_running_loop().run_in_executor(
                None, get_llm_backend_status))

    # ── audit log viewer (core/audit_log.py) ─────────────────────────────

    def _register_audit_routes(self, app: "FastAPI") -> None:
        @app.get("/audit", response_class=HTMLResponse)
        async def audit_page():
            # Auth is the bearer token the page sends to /api/audit (see "/" above).
            return HTMLResponse(_read("audit.html"))

        @app.get("/api/audit")
        async def audit_api(req: Request):
            if not self._auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            from core import audit_log
            qp = req.query_params
            try:
                limit = int(qp.get("limit", "100"))
            except ValueError:
                limit = 100
            since = None
            try:
                if qp.get("hours"):
                    since = time.time() - float(qp["hours"]) * 3600
            except ValueError:
                since = None
            def _load():
                # Reading and hash-verifying the whole log is disk + CPU work: keep it off the loop.
                entries = audit_log.read(
                    since=since, tool=qp.get("tool", "")[:60], decision=qp.get("decision", "")[:20],
                    tier=qp.get("tier", "")[:20], text=qp.get("text", "")[:100], limit=limit)
                ok, msg = audit_log.verify()
                return {"entries": entries, "summary": audit_log.summarize(entries),
                        "chain": {"ok": ok, "message": msg}}
            return JSONResponse(await asyncio.get_running_loop().run_in_executor(None, _load))

    # ── speed / memory / cloud-usage (core/perf_stats.py) ────────────────

    def _register_perf_routes(self, app: "FastAPI") -> None:
        @app.get("/perf", response_class=HTMLResponse)
        async def perf_page():
            return HTMLResponse(_read("perf.html"))

        @app.get("/api/perf")
        async def perf_api(req: Request):
            if not self._auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            from core import perf_stats
            try:
                hours = min(24.0 * 30, max(0.0, float(req.query_params.get("hours", "24"))))
            except ValueError:
                hours = 24.0
            return JSONResponse(await asyncio.get_running_loop().run_in_executor(
                None, perf_stats.full_report, hours))

    # ── voice command routes ─────────────────────────────────────────────

    def _register_command_routes(self, app: "FastAPI") -> None:
        @app.post("/api/command")
        async def command(req: Request):
            if not self._auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body  = await req.json()
            token = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            enc   = body.get("enc", "")
            if enc:
                text = self._decrypt(token, enc)
                if text is None:
                    return JSONResponse({"error": "Decryption failed"}, status_code=400)
            else:
                text = (body.get("text") or "").strip()
            if text:
                self._command_queue.put(text)
                if self._wake_callback:
                    self._wake_callback()
            return JSONResponse({"ok": True})

        @app.post("/api/wake")
        async def wake_ep(req: Request):
            if not self._auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if self._wake_callback:
                self._wake_callback()
            return JSONResponse({"ok": True})

    # ── phone mic real-time audio ────────────────────────────────────────

    def _register_phone_audio_ws(self, app: "FastAPI") -> None:
        # Client (dashboard/static/app.html doMic()) already resamples to
        # 16 kHz mono and sends signed 16-bit PCM frames — the same format
        # main.py's local-mic pipeline expects. _phone_audio_queue is a
        # plain thread-safe queue.Queue (not asyncio.Queue), same pattern
        # as _command_queue above, so main.py's threaded
        # _phone_audio_stt_loop can drain it directly with a blocking
        # queue.get(timeout=...) — see that method for the consumer.
        @app.websocket("/ws/phone-audio")
        async def phone_audio_ws(websocket: WebSocket, ticket: str = ""):
            tok = self._consume_ws_ticket(ticket, "phone-audio")
            if not tok:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Phone microphone connected."}
            ))
            try:
                while True:
                    data = await websocket.receive_bytes()
                    try:
                        self._phone_audio_queue.put_nowait(
                            {"data": data, "mime_type": "audio/pcm"}
                        )
                    except _queue.Full:
                        pass  # drop frame rather than block
            except WebSocketDisconnect:
                pass
            finally:
                asyncio.create_task(self.broadcast(
                    {"type": "sys", "text": "Phone microphone stopped."}
                ))

    # ── file sharing routes ──────────────────────────────────────────────

    def _register_file_routes(self, app: "FastAPI") -> None:
        if _UPLOAD_OK:
            @app.middleware("http")
            async def upload_preflight(req: Request, call_next):
                if req.url.path == "/api/upload" and req.method == "POST":
                    if not self._auth(req):
                        return JSONResponse({"error": "Unauthorized"}, status_code=401)

                    content_length = req.headers.get("content-length")
                    if content_length is None:
                        return JSONResponse(
                            {"error": "Content-Length is required for uploads"},
                            status_code=411,
                        )
                    try:
                        request_size = int(content_length)
                    except ValueError:
                        return JSONResponse({"error": "Invalid Content-Length"}, status_code=400)
                    if request_size < 0:
                        return JSONResponse({"error": "Invalid Content-Length"}, status_code=400)
                    max_request_bytes = MAX_UPLOAD_MB * 1024 * 1024 + 1024 * 1024
                    if request_size > max_request_bytes:
                        return JSONResponse(
                            {"error": f"Upload request too large (max {MAX_UPLOAD_MB} MB file)"},
                            status_code=413,
                        )
                return await call_next(req)

            @app.post("/api/upload")
            async def upload_file(req: Request, file: UploadFile = FastAPIFile(...)):
                if not self._auth(req):
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)

                safe = self._safe_filename(file.filename or "upload")
                dest = self._uploads_dir / safe
                stem, suffix = Path(safe).stem, Path(safe).suffix
                counter = 1
                while dest.exists():
                    dest = self._uploads_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

                size = 0
                max_bytes = MAX_UPLOAD_MB * 1024 * 1024
                try:
                    with open(dest, "wb") as fout:
                        while True:
                            chunk = await file.read(65536)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > max_bytes:
                                fout.close()
                                dest.unlink(missing_ok=True)
                                return JSONResponse(
                                    {"error": f"File too large (max {MAX_UPLOAD_MB} MB)"},
                                    status_code=413,
                                )
                            fout.write(chunk)
                except Exception as exc:
                    try:
                        dest.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return JSONResponse({"error": str(exc)}, status_code=500)

                asyncio.create_task(self.broadcast({
                    "type": "file_received",
                    "name": dest.name,
                    "size": size,
                    "saved_to": str(self._uploads_dir),
                }))
                return JSONResponse({"ok": True, "name": dest.name, "size": size})
        else:
            @app.post("/api/upload")
            async def upload_unavailable(req: Request):
                return JSONResponse(
                    {"error": "File uploads require: pip install python-multipart"},
                    status_code=503,
                )

        @app.get("/api/files")
        async def list_files(req: Request):
            if not self._auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            files = []
            try:
                for f in sorted(
                    (p for p in self._uploads_dir.iterdir() if p.is_file()),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                ):
                    files.append({"name": f.name, "size": f.stat().st_size})
            except Exception:
                pass
            return JSONResponse({"files": files})

        @app.get("/uploads/{filename}")
        async def download_file(filename: str, req: Request):
            if not self._auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            safe = re.sub(r'[/\\]', '', filename)
            path = self._uploads_dir / safe
            if not path.exists() or not path.is_file():
                return JSONResponse({"error": "Not found"}, status_code=404)
            return FileResponse(str(path), filename=safe, headers={"Cache-Control": "no-store"})

    # ── main relay websocket ──────────────────────────────────────────────

    def _register_main_ws(self, app: "FastAPI") -> None:
        @app.websocket("/ws")
        async def ws_ep(websocket: WebSocket, ticket: str = ""):
            tok = self._consume_ws_ticket(ticket, "main")
            if not tok:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            self._clients.add(websocket)
            for entry in self._history[-50:]:
                try:
                    await websocket.send_json(entry)
                except Exception:
                    break
            try:
                while True:
                    data = await websocket.receive_json()
                    if data.get("type") == "command":
                        enc = data.get("enc", "")
                        t   = self._decrypt(tok, enc) if enc else (data.get("text") or "").strip()
                        if t:
                            self._command_queue.put(t)
                            if self._wake_callback:
                                self._wake_callback()
            except WebSocketDisconnect:
                pass
            finally:
                self._clients.discard(websocket)


    # ── serve ─────────────────────────────────────────────────────────────

    async def _serve_alias(self) -> None:
        """Second HTTPS server on PORT+1 sharing the same app and in-memory state.
        Chrome HTTPS-upgrades any bare IP:PORT the user types, so this port also needs TLS.
        User types IP:8001 → Chrome tries https → self-signed cert warning → accept once → done."""
        from core import config_home as _ch
        ssl_key  = _ch.certs_dir() / "aether.key"
        ssl_cert = _ch.certs_dir() / "aether.crt"
        asyncio.get_event_loop().run_in_executor(None, _ensure_network_access, PORT + 1)
        cfg = uvicorn.Config(
            self.app, host=self._host, port=PORT + 1, log_level="warning",
            ssl_keyfile=str(ssl_key), ssl_certfile=str(ssl_cert),
        )
        print(f"[Dashboard] Manual entry:  {self._ip}:{PORT + 1}  (type in browser, accept cert once)")
        await uvicorn.Server(cfg).serve()

    async def serve(self) -> None:
        if not _DEPS_OK:
            print("[Dashboard] fastapi/uvicorn not installed — dashboard disabled.")
            print("[Dashboard] Run:  pip install fastapi 'uvicorn[standard]' cryptography")
            return

        cert_ok  = self._ssl_enabled()
        use_ssl  = self._tls_active()
        from core import config_home as _ch
        ssl_key  = _ch.certs_dir() / "aether.key"
        ssl_cert = _ch.certs_dir() / "aether.crt"
        if not self._remote_access_allowed(self._host, cert_ok):
            print("[Dashboard] Remote access disabled: set up TLS certificates in config/certs first.")
            return

        if use_ssl and not self._is_loopback_host(self._host):
            asyncio.create_task(self._serve_alias())

        if not self._is_loopback_host(self._host):
            asyncio.get_event_loop().run_in_executor(None, _ensure_network_access, PORT)

        cfg = uvicorn.Config(
            self.app, host=self._host, port=PORT, log_level="warning",
            **({"ssl_keyfile": str(ssl_key), "ssl_certfile": str(ssl_cert)} if use_ssl else {}),
        )

        proto = "https" if use_ssl else "http"
        print(f"[Dashboard] {proto}://{self._ip}:{PORT}")
        print("[Dashboard] Press 'Remote Control' in AETHER UI to get the QR code.")
        await uvicorn.Server(cfg).serve()


# ── Aether integration helper ─────────────────────────────────────────────
# Runs the dashboard's asyncio event loop on a background thread so it can
# live alongside Aether's synchronous/threaded main.py.

_dashboard_instance: "DashboardServer | None" = None
_dashboard_loop = None


def start_dashboard_server(wake_callback=None, connect_callback=None) -> "DashboardServer | None":
    """
    Starts the FastAPI dashboard on its own daemon thread with its own
    asyncio event loop. Returns the DashboardServer instance so main.py can:
      - poll  dashboard._command_queue.get(timeout=...)  for remote text commands
      - call  push_log_threadsafe(text)  to mirror assistant/log output to
        any connected browser or phone.
    Safe no-op (returns None) if fastapi/uvicorn aren't installed.
    """
    global _dashboard_instance, _dashboard_loop
    if not _DEPS_OK:
        return None

    import threading

    dashboard = DashboardServer()
    if wake_callback:
        dashboard.set_wake_callback(wake_callback)
    if connect_callback:
        dashboard.set_connect_callback(connect_callback)

    def _run():
        global _dashboard_loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _dashboard_loop = loop
        try:
            loop.run_until_complete(dashboard.serve())
        except Exception as e:
            print(f"[Dashboard] stopped: {e}")

    threading.Thread(target=_run, daemon=True, name="AetherDashboard").start()
    _dashboard_instance = dashboard
    return dashboard


def attach_ui_mirror(dashboard, ui, push=None):
    """Send what the desktop log shows to every connected browser.

    The dashboard page renders {"type": "log", "speaker": ..., "text": ...} and
    {"type": "status", "state": ...} messages, but nothing ever produced them, so commands
    went in and no reply ever came back. This wraps the UI's write_log/set_state so each
    assistant reply and system line is mirrored. Your own lines are skipped (the page already
    shows what you typed). Returns the original callables so a test or caller can restore."""
    push = push or push_log_threadsafe
    original_log, original_state = ui.write_log, ui.set_state

    def _classify(text: str):
        head, sep, rest = text.partition(":")
        tag = head.strip().lower()
        if sep and tag == "aether":
            return {"type": "log", "speaker": "aether", "text": rest.strip()}
        if sep and tag in ("sys", "wrn", "err", "file"):
            return {"type": "sys", "text": (rest.strip() if tag == "sys" else f"{tag.upper()}: {rest.strip()}")}
        if sep and tag == "you":
            return None
        return {"type": "sys", "text": text}

    def write_log(text, *a, **k):
        original_log(text, *a, **k)
        try:
            msg = _classify(str(text))
            if msg and msg["text"]:
                push(dashboard, msg)
        except Exception:      # noqa: BLE001 - mirroring must never break the desktop log
            pass

    def set_state(state, *a, **k):
        original_state(state, *a, **k)
        try:
            st = str(state).upper()
            push(dashboard, {"type": "status",
                             "state": "sleeping" if st == "MUTED"
                             else "thinking" if st in ("THINKING", "PROCESSING") else "active"})
        except Exception:      # noqa: BLE001
            pass

    ui.write_log, ui.set_state = write_log, set_state
    return original_log, original_state


def push_log_threadsafe(dashboard: "DashboardServer", msg: dict) -> None:
    """Call broadcast() from a normal (non-async) thread, e.g. from main.py."""
    if dashboard is None or _dashboard_loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(dashboard.broadcast(msg), _dashboard_loop)
    except Exception:
        pass
