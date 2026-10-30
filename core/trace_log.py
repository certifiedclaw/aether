"""core/trace_log.py - the flight recorder: what Aether did, when, and with what.

One JSON object per line in logs/trace/trace-<session>.jsonl. Every line carries
    seq     global order (never repeats within a session)
    ts      wall-clock time, ISO-8601 with milliseconds and UTC offset
    t       seconds since the session started (monotonic, for exact gaps)
    thread  the thread that emitted it
    turn    the user request it belongs to (t0001, t0002, ...), when known
    kind    what happened (catalogue below)
plus the fields of that event. Nothing is summarised: full prompts, full replies,
full tool arguments and results. Only secrets are removed (secret-named keys and
API-key/token patterns), and a single field is clipped only past
logging.trace_max_field_chars (default 200000), with a visible marker.

Read it with:  python -m tools.trace_view  (list / show / turn / errors / summary)

Kinds
  session_start / session_end   machine, versions, git commit, GPU, redacted config
  turn_start / turn_end         one user request, wall time, error + traceback if any
  route, tools_selected, context_built, context_compress, decision
                                why the turn went the way it did
  llm_request / llm_sentence / llm_done / llm_response / llm_error
                                every model call: exact messages sent, every streamed
                                sentence, final text, tool calls, tokens, timings
  tool.start / tool.end         every tool call: arguments, full result, duration, error
  gate_decision                 permission checks (allowed / approved / denied / blocked)
  http_request / http_response / http_error      every outgoing HTTP call, with timing
  subprocess_start / subprocess_end              every process Aether launches
  stt_transcribe, utterance, listen_mode, barge_in   the voice path, incl. segments Whisper dropped
  speak_queued, speak_start, tts.start/end       what was said and how long it waited
  ui_log, ui_state                what the activity panel showed
  log                             every stdlib logging record (all levels)
  stdout / stderr                 every print and traceback
  heartbeat, stall_dump           liveness; all thread stacks when a turn hangs
  uncaught_exception, crash       anything that escaped

Recording never raises and never blocks the caller: events go through a bounded
queue to a writer thread, and are counted (not waited for) if the queue is full.
Turn it off with config logging.trace_enabled=false or env AETHER_TRACE=0.
"""
from __future__ import annotations

import atexit
import contextvars
import faulthandler
import functools
import itertools
import json
import logging
import os
import platform
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = _ROOT / "logs" / "trace"

# Key names whose VALUE is hidden. Anchored at the end so "prompt_tokens" and
# "max_tokens" (counters) survive while "api_token" / "gemini_api_key" do not.
_SECRET_KEY = re.compile(
    r"(pass(word|phrase|wd)?|secret|token|api[_-]?key|credential|authorization"
    r"|cookie|bearer|private[_-]?key)$|^pin$", re.I)
_FALLBACK_SECRETS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"),
]
_URL_SECRET_PARAM = re.compile(
    r"([?&](?:key|api_?key|apikey|token|access_token|auth|password|secret)=)[^&#]*", re.I)

_NOISY_LIBS = ("urllib3", "httpx", "httpcore", "PIL", "asyncio", "matplotlib", "numba",
               "chromadb", "filelock", "fsspec", "huggingface_hub", "markdown_it",
               "comtypes", "multipart", "charset_normalizer", "hpack")

_PACKAGES = ("requests", "numpy", "sounddevice", "faster-whisper", "PyQt6", "PySide6",
             "psutil", "kokoro", "edge-tts", "openwakeword", "chromadb", "langchain",
             "fastapi", "uvicorn", "torch", "ctranslate2", "vosk", "pyautogui")

_turn_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("aether_turn", default=None)
_tls = threading.local()
_start_lock = threading.Lock()


# ── settings ──────────────────────────────────────────────────────────────

def _as_bool(v: Any, default: bool) -> bool:
    if v is None:
        return default
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _as_num(v: Any, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


class _Settings:
    def __init__(self, cfg: dict | None) -> None:
        cfg = cfg or {}

        def get(name: str, default: Any = None) -> Any:
            if f"logging.{name}" in cfg:
                return cfg[f"logging.{name}"]
            nested = cfg.get("logging")
            if isinstance(nested, dict) and name in nested:
                return nested[name]
            return default

        self.enabled        = _as_bool(get("trace_enabled"), True)
        d = get("trace_dir")
        base = get("log_dir")
        if d:
            self.dir = Path(os.path.expanduser(str(d)))
        elif base:
            self.dir = Path(os.path.expanduser(str(base))) / "trace"
        else:
            self.dir = DEFAULT_DIR
        if not self.dir.is_absolute():
            self.dir = _ROOT / self.dir
        self.keep_sessions  = int(_as_num(get("trace_keep_sessions"), 30, 1, 1000))
        self.max_bytes      = int(_as_num(get("trace_max_mb"), 200, 1, 10_000) * 1024 * 1024)
        self.max_field      = int(_as_num(get("trace_max_field_chars"), 200_000, 200, 50_000_000))
        self.capture_stdio  = _as_bool(get("trace_capture_stdio"), True)
        self.patch_io       = _as_bool(get("trace_patch_io"), True)
        self.heartbeat_s    = _as_num(get("trace_heartbeat_s"), 30.0, 1.0, 3600.0)
        self.stall_s        = _as_num(get("trace_stall_s"), 45.0, 5.0, 3600.0)
        self.level          = _level(get("level"), logging.DEBUG)


def _level(name: Any, default: int) -> int:
    if isinstance(name, int):
        return name
    lv = getattr(logging, str(name or "").strip().upper(), None)
    return lv if isinstance(lv, int) else default


# ── state ─────────────────────────────────────────────────────────────────

class _Flush:
    def __init__(self) -> None:
        self.done = threading.Event()


class _State:
    def __init__(self, st: _Settings, session: str) -> None:
        self.st       = st
        self.session  = session
        self.t0       = time.monotonic()
        self.seq      = itertools.count(1)
        self.span_ids = itertools.count(1)
        self.call_ids = itertools.count(1)
        self.turn_n   = itertools.count(1)
        self.q: queue.Queue = queue.Queue(maxsize=50_000)
        self.dropped  = 0
        self.stop     = threading.Event()
        self.active: dict[str, float] = {}     # turn id -> monotonic start
        self.lock     = threading.Lock()
        self.undo: list = []
        self.seen_tools: set[str] = set()
        self.secrets: list[str] = []           # exact secret values from config
        self.writer: threading.Thread | None = None
        self.beat: threading.Thread | None = None
        self.fault_fh = None
        self.fault_path: Path | None = None
        self.trace_path: Path | None = None


_S: _State | None = None


def is_enabled() -> bool:
    return _S is not None


def session_id() -> str | None:
    return _S.session if _S else None


def trace_path() -> Path | None:
    return _S.trace_path if _S else None


# ── value conversion: JSON-safe, secrets removed, clipped ─────────────────

_redactor = None


def _redact(text: str) -> str:
    global _redactor
    if not text:
        return text
    if _redactor is None:
        try:
            from core.leak_guard import redact_text
            _redactor = redact_text
        except Exception:  # noqa: BLE001
            _redactor = False
    S = _S
    if _redactor:
        try:
            return _redactor(text, S.secrets if S else None)[0]
        except Exception:  # noqa: BLE001
            pass
    for pat in _FALLBACK_SECRETS:
        text = pat.sub("[REDACTED:secret]", text)
    return text


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"…[+{len(text) - limit} chars clipped]"


def _jsonable(v: Any, key: str = "", depth: int = 0, limit: int = 200_000) -> Any:
    if key and _SECRET_KEY.search(key) and v not in (None, "", False):
        return "(hidden)"
    if v is None or isinstance(v, (bool, int)):
        return v
    if isinstance(v, float):
        return v if v == v and v not in (float("inf"), float("-inf")) else str(v)
    if isinstance(v, str):
        return _clip(_redact(v), limit)
    if depth > 8:
        return "<max depth>"
    if isinstance(v, dict):
        out = {}
        for i, (k, val) in enumerate(v.items()):
            if i >= 500:
                out["…"] = f"+{len(v) - 500} more keys"
                break
            out[str(k)] = _jsonable(val, str(k), depth + 1, limit)
        return out
    if isinstance(v, (list, tuple, set, frozenset)):
        items = list(v)
        out = [_jsonable(x, "", depth + 1, limit) for x in items[:500]]
        if len(items) > 500:
            out.append(f"…+{len(items) - 500} more items")
        return out
    if isinstance(v, (bytes, bytearray)):
        return f"<{len(v)} bytes>"
    if isinstance(v, Path):
        return str(v)
    if isinstance(v, BaseException):
        return f"{type(v).__name__}: {v}"
    shape = getattr(v, "shape", None)
    if shape is not None and hasattr(v, "dtype"):
        return f"<array shape={tuple(shape)} dtype={v.dtype}>"
    item = getattr(v, "item", None)
    if callable(item):
        try:
            return _jsonable(item(), key, depth + 1, limit)
        except Exception:  # noqa: BLE001
            pass
    return _clip(_redact(repr(v)), limit)


def sanitize(value: Any, limit: int = 20_000) -> Any:
    """JSON-safe copy of `value` with secret-named keys hidden and key/token patterns
    removed. Works whether or not a trace is running (used by the crash reporter)."""
    return _jsonable(value, "", 0, limit)


def recent_events(n: int = 40, max_bytes: int = 400_000) -> list[dict]:
    """The last `n` events of the running trace, each field clipped to 300 chars.
    Empty list if no trace is running. Flushes first so the tail is current."""
    S = _S
    if S is None or S.trace_path is None:
        return []
    flush(2.0)
    try:
        p = S.trace_path
        size = p.stat().st_size
        with open(p, "rb") as fh:
            fh.seek(max(0, size - max_bytes))
            raw = fh.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for ln in raw[-(n + 1):]:
        try:
            ev = json.loads(ln)
        except json.JSONDecodeError:
            continue                      # first line of the tail may be cut mid-record
        out.append({k: (_clip(v, 300) if isinstance(v, str) else v) for k, v in ev.items()
                    if k not in ("schemas", "messages", "stacks")})
    return out[-n:]


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="milliseconds")


# ── emit ──────────────────────────────────────────────────────────────────

def event(kind: str, /, **fields: Any) -> None:
    """Record one event. Never raises, never blocks."""
    S = _S
    if S is None:
        return
    try:
        rec: dict[str, Any] = {
            "seq":    next(S.seq),
            "ts":     _iso(time.time()),
            "t":      round(time.monotonic() - S.t0, 3),
            "thread": threading.current_thread().name,
            "kind":   kind,
        }
        tid = _turn_var.get()
        if tid:
            rec["turn"] = tid
        else:
            with S.lock:
                live = max(S.active, key=S.active.get) if S.active else None
            if live:
                rec["active_turn"] = live
        limit = S.st.max_field
        for k, v in fields.items():
            rec[k if k not in rec else f"f_{k}"] = _jsonable(v, k, 0, limit)
        line = json.dumps(rec, ensure_ascii=False, default=str, separators=(",", ":"))
        try:
            S.q.put_nowait(line)
        except queue.Full:
            S.dropped += 1
    except Exception:  # noqa: BLE001 - recording must never break the thing it records
        pass


class _Span(dict):
    """Fields added to it while a span is open are written on the .end event."""


@contextmanager
def span(kind: str, /, **fields: Any) -> Iterator[_Span]:
    """Bracket an operation: emits <kind>.start now and <kind>.end (duration, ok,
    error + traceback) when it finishes. Exceptions propagate unchanged."""
    S = _S
    ctx = _Span()
    if S is None:
        yield ctx
        return
    sid = next(S.span_ids)
    t0 = time.monotonic()
    event(f"{kind}.start", span=sid, **fields)
    try:
        yield ctx
    except BaseException as e:  # noqa: BLE001 - re-raised below
        event(f"{kind}.end", span=sid, ok=False, dur_ms=round((time.monotonic() - t0) * 1000, 1),
              error=f"{type(e).__name__}: {e}", traceback=traceback.format_exc(), **ctx)
        raise
    else:
        event(f"{kind}.end", span=sid, ok=True,
              dur_ms=round((time.monotonic() - t0) * 1000, 1), **ctx)


@contextmanager
def turn(user_text: str, /, **fields: Any) -> Iterator[str | None]:
    """One user request. Everything emitted by this thread inside the block is tagged
    with the turn id. Exceptions propagate unchanged."""
    S = _S
    if S is None:
        yield None
        return
    tid = f"t{next(S.turn_n):04d}"
    token = _turn_var.set(tid)
    t0 = time.monotonic()
    with S.lock:
        S.active[tid] = t0
    event("turn_start", user_text=user_text, **fields)
    err = None
    try:
        yield tid
    except BaseException as e:  # noqa: BLE001 - re-raised below
        err = e
        raise
    finally:
        extra = {}
        if err is not None:
            extra = {"error": f"{type(err).__name__}: {err}", "traceback": traceback.format_exc()}
        event("turn_end", ok=err is None, dur_ms=round((time.monotonic() - t0) * 1000, 1), **extra)
        with S.lock:
            S.active.pop(tid, None)
        _turn_var.reset(token)


def current_turn() -> str | None:
    return _turn_var.get()


def new_call_id() -> int:
    """Id tying an llm_request to its responses."""
    return next(_S.call_ids) if _S else 0


def tool_schema_once(tools: list | None) -> list[str]:
    """Names of the tools sent in a request. The full schema is written once per
    session (first time that exact set is seen) instead of on every request."""
    if not tools:
        return []
    names: list[str] = []
    for t in tools:
        fn = t.get("function", t) if isinstance(t, dict) else {}
        names.append(str(fn.get("name", "?")))
    S = _S
    if S is not None:
        try:
            sig = json.dumps(tools, sort_keys=True, default=str)
            h = format(hash(sig) & 0xFFFFFFFF, "08x")
            if h not in S.seen_tools:
                S.seen_tools.add(h)
                event("tool_schemas", hash=h, count=len(names), schemas=tools)
        except Exception:  # noqa: BLE001
            pass
    return names


def flush(timeout: float = 5.0) -> None:
    """Block until everything emitted so far is on disk (call before os._exit)."""
    S = _S
    if S is None or S.writer is None or not S.writer.is_alive():
        return
    m = _Flush()
    try:
        S.q.put(m, timeout=timeout)
    except queue.Full:
        return
    m.done.wait(timeout)


# ── writer thread ─────────────────────────────────────────────────────────

class _Writer(threading.Thread):
    def __init__(self, S: _State) -> None:
        super().__init__(name="aether-trace-writer", daemon=True)
        self.S = S
        self.part = 1
        self.f = None
        self.size = 0
        self._reported_drops = 0

    def _path(self) -> Path:
        s = self.S
        name = f"trace-{s.session}.jsonl" if self.part == 1 else f"trace-{s.session}-p{self.part}.jsonl"
        return s.st.dir / name

    def _open(self) -> None:
        p = self._path()
        self.f = open(p, "a", encoding="utf-8", errors="backslashreplace", buffering=1 << 16)
        self.size = p.stat().st_size if p.exists() else 0
        if self.part == 1:
            self.S.trace_path = p

    def _write(self, line: str) -> None:
        if self.f is None:
            self._open()
        data = line + "\n"
        self.f.write(data)
        self.size += len(data)
        if self.size >= self.S.st.max_bytes:
            self.f.flush()
            self.f.close()
            self.part += 1
            self._open()
            self.f.write(json.dumps({"kind": "session_part", "ts": _iso(time.time()),
                                     "part": self.part, "session": self.S.session}) + "\n")

    def _sync(self) -> None:
        if self.f is not None:
            try:
                self.f.flush()
                os.fsync(self.f.fileno())
            except OSError:
                pass

    def run(self) -> None:
        S = self.S
        while True:
            try:
                item = S.q.get(timeout=0.5)
            except queue.Empty:
                if S.dropped > self._reported_drops:
                    n = S.dropped - self._reported_drops
                    self._reported_drops = S.dropped
                    try:
                        self._write(json.dumps({"kind": "trace_dropped", "ts": _iso(time.time()),
                                                "count": n}))
                    except Exception:  # noqa: BLE001
                        pass
                if self.f is not None:
                    try:
                        self.f.flush()
                    except OSError:
                        pass
                if S.stop.is_set():
                    break
                continue
            try:
                if isinstance(item, _Flush):
                    self._sync()
                    item.done.set()
                else:
                    self._write(item)
                    for _ in range(500):                 # drain a batch before flushing
                        try:
                            nxt = S.q.get_nowait()
                        except queue.Empty:
                            break
                        if isinstance(nxt, _Flush):
                            self._sync()
                            nxt.done.set()
                        else:
                            self._write(nxt)
                    self.f.flush()
            except Exception:  # noqa: BLE001 - a full disk must not kill the app
                if isinstance(item, _Flush):
                    item.done.set()
        if self.f is not None:
            try:
                self._sync()
                self.f.close()
            except OSError:
                pass


# ── hooks: logging, stdio, excepthooks, faulthandler, http, subprocess ────

class _LogBridge(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if getattr(_tls, "busy", False) or record.name.startswith("core.trace_log"):
            return
        _tls.busy = True
        try:
            f: dict[str, Any] = {
                "level": record.levelname, "logger": record.name, "msg": record.getMessage(),
                "src": f"{record.filename}:{record.lineno}", "func": record.funcName,
            }
            if record.exc_info:
                f["exc"] = "".join(traceback.format_exception(*record.exc_info))
            event("log", **f)
        except Exception:  # noqa: BLE001
            pass
        finally:
            _tls.busy = False


class _Tee:
    """Passes everything through to the real stream and records each line."""

    def __init__(self, stream: Any, name: str) -> None:
        self._s = stream
        self._name = name
        self._buf = ""
        self._lk = threading.Lock()

    def write(self, data: Any) -> int:
        n = self._s.write(data) if self._s is not None else len(data)
        if isinstance(data, str) and not getattr(_tls, "busy", False):
            try:
                self._capture(data)
            except Exception:  # noqa: BLE001
                pass
        return n

    def _capture(self, data: str) -> None:
        with self._lk:
            self._buf += data.replace("\r\n", "\n").replace("\r", "\n")
            if "\n" in self._buf:
                parts = self._buf.split("\n")
                lines, self._buf = parts[:-1], parts[-1]
            elif len(self._buf) >= 65536:
                lines, self._buf = [self._buf], ""
            else:
                return
        for ln in lines:
            if ln.strip():
                event(self._name, text=ln)

    def flush(self) -> None:
        if self._s is not None:
            self._s.flush()
        with self._lk:
            rest, self._buf = self._buf, ""
        if rest.strip():
            event(self._name, text=rest)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._s, name)


def _install_logging(S: _State) -> None:
    root = logging.getLogger()
    bridge = _LogBridge(level=logging.DEBUG)
    root.addHandler(bridge)
    old_level = root.level
    if root.level == logging.NOTSET or root.level > S.st.level:
        root.setLevel(S.st.level)
    noisy_old = {}
    for n in _NOISY_LIBS:
        lg = logging.getLogger(n)
        noisy_old[n] = lg.level
        if lg.level == logging.NOTSET or lg.level < logging.INFO:
            lg.setLevel(logging.INFO)

    def undo() -> None:
        root.removeHandler(bridge)
        root.setLevel(old_level)
        for n, lv in noisy_old.items():
            logging.getLogger(n).setLevel(lv)
    S.undo.append(undo)


def _install_stdio(S: _State) -> None:
    out, err = sys.stdout, sys.stderr
    tout, terr = _Tee(out, "stdout"), _Tee(err, "stderr")
    sys.stdout, sys.stderr = tout, terr

    def undo() -> None:
        for tee, orig, attr in ((tout, out, "stdout"), (terr, err, "stderr")):
            try:
                tee.flush()
            except Exception:  # noqa: BLE001
                pass
            if getattr(sys, attr) is tee:
                setattr(sys, attr, orig)
    S.undo.append(undo)


def _install_excepthooks(S: _State) -> None:
    old_sys, old_thread, old_unraisable = sys.excepthook, threading.excepthook, sys.unraisablehook

    def sys_hook(tp, val, tb):
        event("uncaught_exception", where="main", error=f"{tp.__name__}: {val}",
              traceback="".join(traceback.format_exception(tp, val, tb)))
        flush(2.0)
        old_sys(tp, val, tb)

    def thread_hook(args):
        name = args.thread.name if args.thread else "?"
        event("uncaught_exception", where=f"thread:{name}",
              error=f"{args.exc_type.__name__}: {args.exc_value}",
              traceback="".join(traceback.format_exception(
                  args.exc_type, args.exc_value, args.exc_traceback)))
        old_thread(args)

    def unraisable(u):
        event("uncaught_exception", where="unraisable", error=f"{u.exc_type.__name__}: {u.exc_value}",
              obj=repr(u.object), message=u.err_msg)
        old_unraisable(u)

    sys.excepthook, threading.excepthook, sys.unraisablehook = sys_hook, thread_hook, unraisable

    def undo() -> None:
        if sys.excepthook is sys_hook:
            sys.excepthook = old_sys
        if threading.excepthook is thread_hook:
            threading.excepthook = old_thread
        if sys.unraisablehook is unraisable:
            sys.unraisablehook = old_unraisable
    S.undo.append(undo)


def _install_faulthandler(S: _State) -> None:
    """Native crashes (PortAudio, CUDA...) leave no Python exception: faulthandler
    writes every thread's stack to fault-<session>.log. Empty files are deleted."""
    try:
        S.fault_path = S.st.dir / f"fault-{S.session}.log"
        S.fault_fh = open(S.fault_path, "a", encoding="utf-8")
        faulthandler.enable(file=S.fault_fh, all_threads=True)
    except Exception:  # noqa: BLE001
        S.fault_fh = None
        return

    def undo() -> None:
        try:
            faulthandler.disable()
            S.fault_fh.close()
            if S.fault_path and S.fault_path.exists() and S.fault_path.stat().st_size == 0:
                S.fault_path.unlink()
        except Exception:  # noqa: BLE001
            pass
    S.undo.append(undo)


def _safe_url(url: Any) -> str:
    return _URL_SECRET_PARAM.sub(r"\1(hidden)", str(url))


def _install_http(S: _State) -> None:
    try:
        import requests.sessions as rs
    except Exception:  # noqa: BLE001
        return
    orig = rs.Session.request
    if getattr(orig, "_aether_trace", False):
        return

    @functools.wraps(orig)
    def request(self, method, url, *a, **kw):
        t0 = time.monotonic()
        meta = {"method": str(method).upper(), "url": _safe_url(url),
                "timeout": kw.get("timeout"), "stream": bool(kw.get("stream")),
                "header_names": sorted((kw.get("headers") or {}).keys())}
        body = kw.get("json")
        if body is not None:
            try:
                meta["body_chars"] = len(json.dumps(body, default=str))
            except Exception:  # noqa: BLE001
                pass
        elif kw.get("data") is not None:
            try:
                meta["body_chars"] = len(kw["data"])
            except Exception:  # noqa: BLE001
                pass
        cid = next(S.call_ids)
        event("http_request", call=f"h{cid}", **meta)
        try:
            resp = orig(self, method, url, *a, **kw)
        except BaseException as e:  # noqa: BLE001 - re-raised
            event("http_error", call=f"h{cid}", url=meta["url"], dur_ms=round((time.monotonic() - t0) * 1000, 1),
                  error=f"{type(e).__name__}: {e}")
            raise
        try:
            event("http_response", call=f"h{cid}", url=meta["url"], status=resp.status_code,
                  reason=resp.reason, headers_ms=round((time.monotonic() - t0) * 1000, 1),
                  content_type=resp.headers.get("Content-Type"),
                  content_length=resp.headers.get("Content-Length"))
        except Exception:  # noqa: BLE001
            pass
        return resp

    request._aether_trace = True
    rs.Session.request = request

    def undo() -> None:
        if rs.Session.request is request:
            rs.Session.request = orig
    S.undo.append(undo)


def _install_subprocess(S: _State) -> None:
    orig_init, orig_run = subprocess.Popen.__init__, subprocess.run
    if getattr(orig_init, "_aether_trace", False):
        return

    @functools.wraps(orig_init)
    def init(self, *a, **kw):
        cmd = a[0] if a else kw.get("args")
        meta = {"cmd": cmd, "shell": bool(kw.get("shell")), "cwd": kw.get("cwd")}
        try:
            orig_init(self, *a, **kw)
        except BaseException as e:  # noqa: BLE001 - re-raised
            event("subprocess_error", error=f"{type(e).__name__}: {e}", **meta)
            raise
        event("subprocess_start", pid=getattr(self, "pid", None), **meta)

    @functools.wraps(orig_run)
    def run(*a, **kw):
        t0 = time.monotonic()
        cmd = a[0] if a else kw.get("args")
        try:
            cp = orig_run(*a, **kw)
        except BaseException as e:  # noqa: BLE001 - re-raised
            event("subprocess_end", cmd=cmd, ok=False, error=f"{type(e).__name__}: {e}",
                  dur_ms=round((time.monotonic() - t0) * 1000, 1))
            raise
        event("subprocess_end", cmd=cmd, ok=True, returncode=cp.returncode,
              dur_ms=round((time.monotonic() - t0) * 1000, 1), stdout=cp.stdout, stderr=cp.stderr)
        return cp

    init._aether_trace = True
    subprocess.Popen.__init__ = init
    subprocess.run = run

    def undo() -> None:
        if subprocess.Popen.__init__ is init:
            subprocess.Popen.__init__ = orig_init
        if subprocess.run is run:
            subprocess.run = orig_run
    S.undo.append(undo)


# ── liveness: heartbeat + stall dump ──────────────────────────────────────

def _all_stacks() -> dict[str, str]:
    names = {t.ident: t.name for t in threading.enumerate()}
    out = {}
    for ident, frame in sys._current_frames().items():
        out[f"{names.get(ident, '?')}#{ident}"] = "".join(traceback.format_stack(frame, limit=25))
    return out


def _beat_loop(S: _State) -> None:
    proc = None
    try:
        import psutil
        proc = psutil.Process()
        proc.cpu_percent(None)
    except Exception:  # noqa: BLE001
        pass
    next_beat = time.monotonic() + S.st.heartbeat_s
    dumped: dict[str, tuple[int, float]] = {}
    while not S.stop.wait(min(5.0, S.st.heartbeat_s)):
        now = time.monotonic()
        try:
            if now >= next_beat:
                next_beat = now + S.st.heartbeat_s
                f: dict[str, Any] = {"threads": threading.active_count(),
                                     "queue": S.q.qsize(), "dropped": S.dropped}
                with S.lock:
                    f["active_turns"] = {k: round(now - v, 1) for k, v in S.active.items()}
                if proc is not None:
                    try:
                        f["rss_mb"] = round(proc.memory_info().rss / 1048576, 1)
                        f["cpu_pct"] = proc.cpu_percent(None)
                    except Exception:  # noqa: BLE001
                        pass
                event("heartbeat", **f)
            with S.lock:
                live = dict(S.active)
            for tid, t0 in live.items():
                age = now - t0
                cnt, last = dumped.get(tid, (0, 0.0))
                if age >= S.st.stall_s and cnt < 5 and now - last >= S.st.stall_s:
                    dumped[tid] = (cnt + 1, now)
                    event("stall_dump", stalled_turn=tid, age_s=round(age, 1), stacks=_all_stacks())
        except Exception:  # noqa: BLE001
            pass


# ── session start / stop ──────────────────────────────────────────────────

def _git_commit() -> dict:
    try:
        r = subprocess.run(["git", "-C", str(_ROOT), "rev-parse", "HEAD"], capture_output=True,
                           text=True, timeout=3)
        d = subprocess.run(["git", "-C", str(_ROOT), "status", "--porcelain"], capture_output=True,
                           text=True, timeout=3)
        return {"commit": r.stdout.strip(), "dirty_files": len(d.stdout.splitlines())}
    except Exception:  # noqa: BLE001
        return {}


def _gpu() -> str:
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                            "--format=csv,noheader"], capture_output=True, text=True, timeout=3)
        return r.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def _versions() -> dict:
    out = {}
    try:
        from importlib import metadata
        for name in _PACKAGES:
            try:
                out[name] = metadata.version(name)
            except metadata.PackageNotFoundError:
                pass
    except Exception:  # noqa: BLE001
        pass
    return out


def _snapshot(cfg: dict | None) -> dict:
    snap: dict[str, Any] = {
        "python": sys.version, "executable": sys.executable, "platform": platform.platform(),
        "machine": platform.machine(), "cwd": os.getcwd(), "argv": sys.argv, "pid": os.getpid(),
        "env": {k: v for k, v in os.environ.items() if k.startswith("AETHER_")},
        "packages": _versions(), "git": _git_commit(), "gpu": _gpu(),
        "config": cfg or {},
    }
    try:
        import psutil
        vm = psutil.virtual_memory()
        snap["cpu_count"] = psutil.cpu_count()
        snap["ram_total_mb"] = vm.total // 1048576
        snap["ram_used_mb"] = vm.used // 1048576
    except Exception:  # noqa: BLE001
        pass
    return snap


def _prune(directory: Path, keep: int, current: str) -> None:
    try:
        groups: dict[str, list[Path]] = {}
        for p in list(directory.glob("trace-*")) + list(directory.glob("fault-*")):
            m = re.match(r"(?:trace|fault)-(\d{8}-\d{6}(?:-\d+)?)", p.name)
            if m:
                groups.setdefault(m.group(1), []).append(p)
        for sid in sorted(groups, reverse=True)[keep:]:
            if sid == current:
                continue
            for p in groups[sid]:
                try:
                    p.unlink()
                except OSError:
                    pass
    except Exception:  # noqa: BLE001
        pass


def _load_cfg() -> dict:
    try:
        from core.llm_client import _load_config
        return _load_config() or {}
    except Exception:  # noqa: BLE001
        return {}


def start(config: dict | None = None, *, trace_dir: Path | str | None = None,
          install_hooks: bool = True) -> bool:
    """Begin recording. Idempotent. Returns True when a trace is running."""
    global _S
    with _start_lock:
        if _S is not None:
            return True
        if os.environ.get("AETHER_TRACE", "").strip().lower() in ("0", "false", "no", "off"):
            return False
        try:
            cfg = config if config is not None else _load_cfg()
            st = _Settings(cfg)
            if trace_dir is not None:
                st.dir = Path(trace_dir)
            if not st.enabled:
                return False
            st.dir.mkdir(parents=True, exist_ok=True)
            base = datetime.now().strftime("%Y%m%d-%H%M%S")
            session, n = base, 1
            while (st.dir / f"trace-{session}.jsonl").exists():
                session = f"{base}-{n}"
                n += 1
            S = _State(st, session)
            try:
                from core.leak_guard import known_secrets
                S.secrets = known_secrets(cfg)
            except Exception:  # noqa: BLE001
                S.secrets = []
            S.trace_path = st.dir / f"trace-{session}.jsonl"
            w = _Writer(S)
            S.writer = w
            w.start()
            _S = S
        except Exception:  # noqa: BLE001 - tracing is best-effort
            _S = None
            return False

        try:
            (st.dir / "latest.txt").write_text(session + "\n", encoding="utf-8")
        except OSError:
            pass
        event("session_start", session=session, trace_file=str(S.trace_path),
              settings={"max_field_chars": st.max_field, "keep_sessions": st.keep_sessions,
                        "heartbeat_s": st.heartbeat_s, "stall_s": st.stall_s,
                        "capture_stdio": st.capture_stdio, "patch_io": st.patch_io},
              **_snapshot(cfg))
        if install_hooks:
            for installer, on in ((_install_logging, True), (_install_excepthooks, True),
                                  (_install_faulthandler, True),
                                  (_install_stdio, st.capture_stdio),
                                  (_install_http, st.patch_io), (_install_subprocess, st.patch_io)):
                if on:
                    try:
                        installer(S)
                    except Exception as e:  # noqa: BLE001
                        event("trace_hook_failed", hook=installer.__name__, error=str(e))
            S.beat = threading.Thread(target=_beat_loop, args=(S,), name="aether-trace-heartbeat",
                                      daemon=True)
            S.beat.start()
        _prune(st.dir, st.keep_sessions, session)
        atexit.register(shutdown)
        return True


def shutdown() -> None:
    """Write session_end, restore every hook, flush and close. Safe to call twice."""
    global _S
    S = _S
    if S is None:
        return
    event("session_end", seconds=round(time.monotonic() - S.t0, 1), dropped=S.dropped)
    flush(3.0)
    S.stop.set()
    for undo in reversed(S.undo):
        try:
            undo()
        except Exception:  # noqa: BLE001
            pass
    if S.beat is not None:
        S.beat.join(timeout=2)
    if S.writer is not None:
        S.writer.join(timeout=5)
    _S = None
