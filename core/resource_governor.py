"""core/resource_governor.py - back off when the machine is busy (roadmap #46).

Aether shares its host (Palworld, ~40 containers, or a game on the desktop).
Background work that nobody is waiting for - scheduled tasks, proactive
check-ins - yields while the HOST is under pressure. User turns are never
blocked.

Pressure is judged on a short moving average with hysteresis, so a brief spike
doesn't flap the state:
    busy      host CPU average >= governor_cpu_busy (default 85 %)
              or free RAM < governor_ram_min_free_mb (default 1500 MB)
    ok        again once CPU is 10 points below the threshold and RAM is 20 % above its floor
A deferred job still runs after governor_max_defer_s (default 3600) so reminders
and standing tasks can't starve.

Config (config/api_keys.json): governor_enabled (true), governor_cpu_busy,
governor_ram_min_free_mb, governor_max_defer_s.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

_WINDOW = 6            # samples in the moving average
_MIN_GAP_S = 3.0       # don't sample more often than this
_HYSTERESIS_CPU = 10.0
_HYSTERESIS_RAM = 1.2


@dataclass(frozen=True)
class Pressure:
    busy: bool
    cpu_avg: float
    ram_free_mb: float
    reason: str = ""


def _default_sampler() -> tuple[float, float]:
    """(host cpu %, host free RAM in MB). Raises if psutil is unavailable."""
    import psutil
    return psutil.cpu_percent(interval=None), psutil.virtual_memory().available / (1024 * 1024)


def _num(cfg: dict, key: str, default: float) -> float:
    try:
        v = float(cfg.get(key, default))
        return v if v >= 0 else default
    except (TypeError, ValueError):
        return default


class ResourceGovernor:
    def __init__(self, get_cfg: Callable[[], dict] | None = None,
                 sampler: Callable[[], tuple[float, float]] = _default_sampler,
                 clock: Callable[[], float] = time.monotonic):
        self._get_cfg = get_cfg or (lambda: {})
        self._sampler = sampler
        self._clock = clock
        self._lock = threading.Lock()
        self._cpu: deque[float] = deque(maxlen=_WINDOW)
        self._last_sample = -1e9
        self._busy = False
        self._ram = 0.0
        self.deferred = 0
        self.forced = 0
        try:
            self._sampler()      # prime psutil's cpu counter (first reading is meaningless)
        except Exception:        # noqa: BLE001
            pass

    def _cfg(self) -> dict:
        try:
            return self._get_cfg() or {}
        except Exception:        # noqa: BLE001
            return {}

    def enabled(self) -> bool:
        v = self._cfg().get("governor_enabled", True)
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

    def pressure(self) -> Pressure:
        cfg = self._cfg()
        cpu_hi = _num(cfg, "governor_cpu_busy", 85.0)
        ram_lo = _num(cfg, "governor_ram_min_free_mb", 1500.0)
        with self._lock:
            now = self._clock()
            if now - self._last_sample >= _MIN_GAP_S:
                self._last_sample = now
                try:
                    cpu, ram = self._sampler()
                    self._cpu.append(float(cpu))
                    self._ram = float(ram)
                except Exception:   # noqa: BLE001 - no psutil / odd platform: never block work
                    return Pressure(False, 0.0, 0.0, "no sensor")
            avg = sum(self._cpu) / len(self._cpu) if self._cpu else 0.0
            reasons = []
            if self._busy:
                still_cpu = avg > cpu_hi - _HYSTERESIS_CPU
                still_ram = self._ram < ram_lo * _HYSTERESIS_RAM
                self._busy = still_cpu or still_ram
                if still_cpu:
                    reasons.append(f"CPU {avg:.0f}%")
                if still_ram:
                    reasons.append(f"free RAM {self._ram:.0f} MB")
            else:
                if avg >= cpu_hi:
                    reasons.append(f"CPU {avg:.0f}% >= {cpu_hi:.0f}%")
                if self._ram < ram_lo:
                    reasons.append(f"free RAM {self._ram:.0f} MB < {ram_lo:.0f} MB")
                self._busy = bool(reasons)
            return Pressure(self._busy, avg, self._ram, ", ".join(reasons))

    def allow_background(self, waiting_s: float = 0.0) -> bool:
        """May a background job run now? waiting_s = how long it has already been deferred."""
        if not self.enabled():
            return True
        p = self.pressure()
        if not p.busy:
            return True
        if waiting_s >= _num(self._cfg(), "governor_max_defer_s", 3600.0):
            self.forced += 1
            return True
        self.deferred += 1
        return False

    def status(self) -> str:
        if not self.enabled():
            return "Resource governor: disabled (governor_enabled=false)."
        p = self.pressure()
        state = f"BUSY ({p.reason}) - background jobs are paused" if p.busy else "OK - background jobs allowed"
        return (f"Resource governor: {state}\n"
                f"  host CPU avg {p.cpu_avg:.0f}%, free RAM {p.ram_free_mb:.0f} MB\n"
                f"  deferred so far: {self.deferred}, forced after max wait: {self.forced}")


_instance: ResourceGovernor | None = None
_instance_lock = threading.Lock()


def get_governor() -> ResourceGovernor:
    global _instance
    with _instance_lock:
        if _instance is None:
            def cfg():
                from core.llm_client import _load_config
                return _load_config()
            _instance = ResourceGovernor(cfg)
        return _instance
