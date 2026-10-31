"""core/supervisor.py - self-healing watchdog for the always-on deploy (roadmap #99).

Run it next to Aether (headless / Docker):   python -m core.supervisor

Each cycle probes Ollama (/api/tags), the Aether dashboard, free disk and free RAM.
A failing probe is recovered ONLY if a restart command is configured for it, with
guard rails so it can never become a restart loop:

  * exponential backoff between attempts (base 30 s, doubling, capped at 15 min)
  * at most `max_restarts_per_hour` attempts per probe
  * after a restart the probe gets a grace period before it is judged again
  * ntfy alert (core/notify.py) only after `alert_after` consecutive failed cycles
    with recovery exhausted or unavailable -- one alert per outage, plus one
    "recovered" note when it comes back. A fix that works stays silent.

Every probe failure / restart / alert / recovery is written to the audit log
(tool="supervisor"), so /audit shows what the watchdog did while nobody was looking.

Config (config/api_keys.json), all optional:
  supervisor_interval_s        30
  supervisor_ollama_restart_cmd   e.g. "docker restart ollama"   (argv list or string,
  supervisor_aether_restart_cmd   e.g. "supervisorctl restart aether"   run WITHOUT a shell)
  supervisor_disk_min_free_pct 5        supervisor_disk_path "."
  supervisor_ram_min_free_mb   300      (0 disables)
  supervisor_alert_after       3        supervisor_max_restarts_per_hour 4

Disk and RAM probes have no automatic fix: they only alert.
"""
from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable

import requests

logger = logging.getLogger("aether.supervisor")

BACKOFF_BASE_S = 30.0
BACKOFF_CAP_S = 900.0
GRACE_AFTER_RESTART_S = 60.0


@dataclass
class Probe:
    name: str
    check: Callable[[], tuple[bool, str]]
    recover: Callable[[], bool] | None = None   # None => alert-only


@dataclass
class _State:
    failures: int = 0                 # consecutive failed cycles
    restarts: list[float] = field(default_factory=list)   # attempt timestamps
    next_attempt: float = 0.0
    grace_until: float = 0.0
    alerted: bool = False
    last_detail: str = ""


class Supervisor:
    def __init__(self, probes: list[Probe], *, alert_after: int = 3,
                 max_restarts_per_hour: int = 4, clock: Callable[[], float] = time.time,
                 notify: Callable[..., bool] | None = None,
                 audit: Callable[..., None] | None = None):
        self.probes = probes
        self.alert_after = max(1, int(alert_after))
        self.max_per_hour = max(0, int(max_restarts_per_hour))
        self._clock = clock
        self._notify = notify or _default_notify
        self._audit = audit or _default_audit
        self.state = {p.name: _State() for p in probes}

    # ── one pass over every probe ────────────────────────────────────────
    def run_once(self) -> dict[str, str]:
        """Returns {probe: "ok"|"failing"|"restarted"|"backoff"|"gave_up"|"grace"}."""
        out: dict[str, str] = {}
        for p in self.probes:
            try:
                out[p.name] = self._cycle(p)
            except Exception as e:  # noqa: BLE001 - one broken probe must not stop the rest
                logger.warning("supervisor probe %s crashed: %s", p.name, e)
                out[p.name] = "failing"
        return out

    def _cycle(self, p: Probe) -> str:
        st, now = self.state[p.name], self._clock()
        try:
            ok, detail = p.check()
        except Exception as e:  # noqa: BLE001 - a raising probe counts as a failure
            ok, detail = False, f"probe error: {e}"
        st.last_detail = detail

        if ok:
            if st.failures:
                self._audit("supervisor", "recovered", "system", "ok",
                            {"probe": p.name, "after_failures": st.failures})
                if st.alerted:
                    self._notify(f"{p.name} is healthy again.", title="Aether supervisor",
                                 priority="default")
            self.state[p.name] = _State()
            return "ok"

        if now < st.grace_until:
            return "grace"          # just restarted: give it time, don't count it

        st.failures += 1
        if st.failures == 1:
            self._audit("supervisor", "probe_failed", "system", "failing",
                        {"probe": p.name}, note=detail)
        result = "failing"

        if p.recover is not None:
            st.restarts = [t for t in st.restarts if now - t < 3600]
            if len(st.restarts) >= self.max_per_hour:
                result = "gave_up"
            elif now < st.next_attempt:
                result = "backoff"
            else:
                st.restarts.append(now)
                delay = min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** (len(st.restarts) - 1)))
                st.next_attempt = now + delay
                fixed = False
                try:
                    fixed = bool(p.recover())
                except Exception as e:  # noqa: BLE001
                    detail = f"{detail}; restart error: {e}"
                self._audit("supervisor", "restart", "system",
                            "issued" if fixed else "failed", {"probe": p.name},
                            note=detail)
                if fixed:
                    st.grace_until = now + GRACE_AFTER_RESTART_S
                    # Not counted against the alert threshold until grace passes
                    # and the probe still fails.
                    st.failures = max(0, st.failures - 1)
                    return "restarted"

        # With a restart command, stay quiet until recovery is exhausted ("gave_up").
        recovery_pending = p.recover is not None and result != "gave_up"
        if st.failures >= self.alert_after and not st.alerted and not recovery_pending:
            st.alerted = True
            msg = f"{p.name} is down and could not be fixed automatically: {detail}"
            self._audit("supervisor", "alert", "system", "sent", {"probe": p.name}, note=detail)
            self._notify(msg, title="Aether needs attention", priority="high")
        return result

    def run_forever(self, interval_s: float = 30.0, stop=None) -> None:
        while not (stop and stop.is_set()):
            self.run_once()
            time.sleep(interval_s)


# ── default wiring ───────────────────────────────────────────────────────
def _default_notify(message: str, **kw) -> bool:
    from core.notify import send_ntfy
    return send_ntfy(message, **kw)


def _default_audit(tool, action, tier, decision, params=None, note="") -> None:
    from core.audit_log import record
    record(tool, action, tier, decision, params, note=note)


def _argv(cmd) -> list[str]:
    if isinstance(cmd, (list, tuple)):
        return [str(c) for c in cmd]
    return shlex.split(str(cmd or ""))


def make_restart(cmd, runner=subprocess.run) -> Callable[[], bool] | None:
    """Restart callable for a configured command, run with no shell. None if unset."""
    argv = _argv(cmd)
    if not argv:
        return None

    def _restart() -> bool:
        r = runner(argv, capture_output=True, timeout=120, check=False)
        return getattr(r, "returncode", 1) == 0
    return _restart


def http_probe(name: str, url: str, *, timeout: float = 5.0, verify: bool = True,
               get=requests.get) -> Callable[[], tuple[bool, str]]:
    def _check() -> tuple[bool, str]:
        try:
            r = get(url, timeout=timeout, verify=verify)
        except Exception as e:  # noqa: BLE001
            return False, f"{name} unreachable at {url}: {type(e).__name__}"
        return (r.status_code < 500, f"{name} HTTP {r.status_code}")
    return _check


def disk_probe(path: str = ".", min_free_pct: float = 5.0,
               usage=shutil.disk_usage) -> Callable[[], tuple[bool, str]]:
    def _check() -> tuple[bool, str]:
        u = usage(path)
        pct = 100.0 * u.free / u.total if u.total else 100.0
        return pct >= min_free_pct, f"disk {pct:.1f}% free (min {min_free_pct:g}%)"
    return _check


def ram_probe(min_free_mb: float = 300.0, available=None) -> Callable[[], tuple[bool, str]]:
    def _avail() -> float:
        import psutil
        return psutil.virtual_memory().available / (1024 * 1024)

    def _check() -> tuple[bool, str]:
        try:
            mb = (available or _avail)()
        except Exception:  # noqa: BLE001 - no psutil: can't judge, don't alarm
            return True, "ram check unavailable"
        return mb >= min_free_mb, f"{mb:.0f} MB RAM free (min {min_free_mb:g})"
    return _check


def build_from_config(cfg: dict) -> Supervisor:
    ollama = (cfg.get("llm_local_url") or cfg.get("ollama_url") or cfg.get("llm_url")
              or os.environ.get("OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
    port = os.environ.get("AETHER_DASHBOARD_PORT", "8000")
    probes = [
        Probe("ollama", http_probe("ollama", f"{ollama}/api/tags"),
              make_restart(cfg.get("supervisor_ollama_restart_cmd"))),
        Probe("aether", http_probe("aether dashboard", f"https://127.0.0.1:{port}/login",
                                   verify=False),
              make_restart(cfg.get("supervisor_aether_restart_cmd"))),
        Probe("disk", disk_probe(cfg.get("supervisor_disk_path", "."),
                                 float(cfg.get("supervisor_disk_min_free_pct", 5)))),
    ]
    if float(cfg.get("supervisor_ram_min_free_mb", 300)) > 0:
        probes.append(Probe("ram", ram_probe(float(cfg.get("supervisor_ram_min_free_mb", 300)))))
    return Supervisor(probes, alert_after=int(cfg.get("supervisor_alert_after", 3)),
                      max_restarts_per_hour=int(cfg.get("supervisor_max_restarts_per_hour", 4)))


def main() -> None:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)  # local self-signed cert
    from core.llm_client import _load_config
    cfg = _load_config() or {}
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    Supervisor.run_forever(build_from_config(cfg),
                           float(cfg.get("supervisor_interval_s", 30)))


if __name__ == "__main__":
    main()
