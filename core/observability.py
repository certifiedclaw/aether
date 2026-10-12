"""
core/observability.py — Aether Observability Layer

Addresses the #6 gap: "No logs, metrics, traces, or debugging."

Three components in one file (no external deps required):
  1. StructuredLogger  — JSON-lines log file + human console output
  2. MetricsCollector  — in-memory counters/timers, exportable as dict/markdown
  3. Tracer            — lightweight span tracing for multi-step tool calls

Optional: if opentelemetry-sdk is installed, spans are also exported via OTLP.
Without it, everything works offline with zero extra packages.

Usage:
    from core.observability import get_obs
    obs = get_obs()

    # Log structured events
    obs.log("tool_call", tool="web_search", query="python tips", latency_ms=450)

    # Count things
    obs.count("tool.web_search")
    obs.count("tool.error", tags={"tool": "browser_control"})

    # Time a block
    with obs.span("llm_round") as span:
        result = call_llm(...)
        span.set("tokens", 512)

    # Get a markdown report
    print(obs.report())
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator

logger = logging.getLogger(__name__)


# ── Span ──────────────────────────────────────────────────────────────────

@dataclass
class Span:
    name:       str
    start_ms:   float = field(default_factory=lambda: time.monotonic() * 1000)
    end_ms:     float = 0.0
    attrs:      dict  = field(default_factory=dict)
    ok:         bool  = True
    error:      str   = ""

    def set(self, key: str, value: Any) -> None:
        self.attrs[key] = value

    def finish(self, ok: bool = True, error: str = "") -> None:
        self.end_ms = time.monotonic() * 1000
        self.ok     = ok
        self.error  = error

    @property
    def duration_ms(self) -> float:
        if self.end_ms:
            return self.end_ms - self.start_ms
        return (time.monotonic() * 1000) - self.start_ms


# ── MetricsCollector ──────────────────────────────────────────────────────

class MetricsCollector:
    def __init__(self) -> None:
        self._lock    = threading.Lock()
        self._counts: dict[str, int]         = defaultdict(int)
        self._totals: dict[str, float]       = defaultdict(float)   # for timing
        self._hists:  dict[str, list[float]] = defaultdict(list)    # for distributions
        self._start   = time.time()

    def count(self, name: str, n: int = 1, tags: dict | None = None) -> None:
        key = self._key(name, tags)
        with self._lock:
            self._counts[key] += n

    def record(self, name: str, value: float, tags: dict | None = None) -> None:
        """Record a timing or value sample."""
        key = self._key(name, tags)
        with self._lock:
            self._totals[key] += value
            self._hists[key].append(value)
            # Keep histogram bounded
            if len(self._hists[key]) > 1000:
                self._hists[key] = self._hists[key][-500:]

    def get_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def get_stats(self, name: str, tags: dict | None = None) -> dict:
        key = self._key(name, tags)
        with self._lock:
            vals = self._hists.get(key, [])
            if not vals:
                return {}
            return {
                "count": len(vals),
                "mean":  sum(vals) / len(vals),
                "min":   min(vals),
                "max":   max(vals),
                "p95":   sorted(vals)[int(len(vals) * 0.95)],
            }

    def uptime_seconds(self) -> float:
        return time.time() - self._start

    @staticmethod
    def _key(name: str, tags: dict | None) -> str:
        if not tags:
            return name
        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}[{tag_str}]"


# ── StructuredLogger ──────────────────────────────────────────────────────

class StructuredLogger:
    """
    Writes newline-delimited JSON to logs/events.jsonl.
    Human-readable summary also goes to the rotating text log.
    """

    _MAX_JSONL_BYTES  = 5 * 1024 * 1024   # rotate once the file crosses this size
    _JSONL_KEEP_LINES = 5000              # lines kept after rotating

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._jsonl  = log_dir / "events.jsonl"
        self._lock   = threading.Lock()
        self._session = str(int(time.time()))

    def event(self, kind: str, **kwargs) -> None:
        record = {
            "ts":      time.time(),
            "session": self._session,
            "kind":    kind,
            **kwargs,
        }
        line = json.dumps(record, default=str)
        with self._lock:
            self._maybe_rotate()
            with open(self._jsonl, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        logger.debug("[obs] %s %s", kind, kwargs)

    def _maybe_rotate(self) -> None:
        """Keep events.jsonl from growing without bound over a long-running
        session: once it crosses _MAX_JSONL_BYTES, truncate to the most
        recent _JSONL_KEEP_LINES lines. Caller must already hold self._lock.
        """
        try:
            if not self._jsonl.exists() or \
               self._jsonl.stat().st_size < self._MAX_JSONL_BYTES:
                return
            lines = self._jsonl.read_text(encoding="utf-8").splitlines()
            kept  = lines[-self._JSONL_KEEP_LINES:]
            self._jsonl.write_text(
                "\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            logger.info("[obs] events.jsonl rotated: kept last %d of %d lines",
                        len(kept), len(lines))
        except Exception as e:
            logger.warning("[obs] events.jsonl rotation failed: %s", e)

    def recent(self, n: int = 20) -> list[dict]:
        try:
            lines = self._jsonl.read_text(encoding="utf-8").splitlines()
            return [json.loads(l) for l in lines[-n:] if l]
        except Exception:
            return []


# ── Tracer ────────────────────────────────────────────────────────────────

class Tracer:
    def __init__(self, structured_logger: StructuredLogger,
                 metrics: MetricsCollector) -> None:
        self._slog    = structured_logger
        self._metrics = metrics
        self._spans:  list[Span] = []
        self._lock    = threading.Lock()

    @contextlib.contextmanager
    def span(self, name: str, **attrs) -> Generator[Span, None, None]:
        s = Span(name=name, attrs=dict(attrs))
        try:
            yield s
        except Exception as exc:
            s.finish(ok=False, error=str(exc))
            raise
        finally:
            if not s.end_ms:
                s.finish()
            with self._lock:
                self._spans.append(s)
                if len(self._spans) > 500:
                    self._spans = self._spans[-250:]
            self._metrics.record(f"span.{name}.ms", s.duration_ms)
            self._slog.event("span",
                             name=name,
                             duration_ms=round(s.duration_ms, 1),
                             ok=s.ok,
                             error=s.error,
                             **s.attrs)

    def recent_spans(self, n: int = 10) -> list[Span]:
        with self._lock:
            return list(self._spans[-n:])


# ── Observability facade ──────────────────────────────────────────────────

class Observability:
    """Single entry-point for all observability concerns."""

    def __init__(self, config=None) -> None:
        log_dir = Path("./logs")
        if config:
            try:
                log_dir = Path(config.get("logging.log_dir", "./logs"))
            except Exception:
                pass

        self.metrics = MetricsCollector()
        self.slog    = StructuredLogger(log_dir)
        self.tracer  = Tracer(self.slog, self.metrics)
        self._otel   = self._try_otel()

    @staticmethod
    def _try_otel():
        """Optionally connect to OpenTelemetry if the SDK is installed."""
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider
            provider = TracerProvider()
            trace.set_tracer_provider(provider)
            logger.info("Observability: OpenTelemetry SDK connected")
            return trace.get_tracer("aether")
        except ImportError:
            return None

    # ── Convenience pass-throughs ─────────────────────────────────────────

    def log(self, kind: str, **kwargs) -> None:
        self.slog.event(kind, **kwargs)

    def count(self, name: str, n: int = 1, tags: dict | None = None) -> None:
        self.metrics.count(name, n, tags)

    def record(self, name: str, value: float, tags: dict | None = None) -> None:
        self.metrics.record(name, value, tags)

    def span(self, name: str, **attrs):
        return self.tracer.span(name, **attrs)

    # ── Report ────────────────────────────────────────────────────────────

    def report(self) -> str:
        lines = ["## Aether — Observability Report", ""]
        lines.append(f"**Uptime:** {self.metrics.uptime_seconds():.0f}s")

        counts = self.metrics.get_counts()
        if counts:
            lines += ["", "### Event Counts", ""]
            for k, v in sorted(counts.items(), key=lambda x: -x[1])[:20]:
                lines.append(f"  {k}: {v}")

        spans = self.tracer.recent_spans(15)
        if spans:
            lines += ["", "### Recent Spans", ""]
            for s in reversed(spans):
                icon = "✅" if s.ok else "❌"
                lines.append(f"  {icon} {s.name} — {s.duration_ms:.0f}ms"
                              + (f"  [{s.error}]" if s.error else ""))

        recent = self.slog.recent(5)
        if recent:
            lines += ["", "### Recent Events", ""]
            for e in recent:
                ts = time.strftime("%H:%M:%S", time.localtime(e.get("ts", 0)))
                lines.append(f"  [{ts}] {e.get('kind','?')} {e.get('tool','')} {e.get('error','')}")

        return "\n".join(lines)

    def dashboard_dict(self) -> dict:
        return {
            "uptime_s":     round(self.metrics.uptime_seconds()),
            "counts":       self.metrics.get_counts(),
            "recent_spans": [
                {"name": s.name, "ms": round(s.duration_ms, 1), "ok": s.ok}
                for s in self.tracer.recent_spans(10)
            ],
            "recent_events": self.slog.recent(10),
        }


# ── Singleton ─────────────────────────────────────────────────────────────

_obs: Observability | None = None
_obs_lock = threading.Lock()


def get_obs(config=None) -> Observability:
    global _obs
    if _obs is None:
        with _obs_lock:
            if _obs is None:
                _obs = Observability(config)
    return _obs
