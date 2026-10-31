"""Tests for core/supervisor.py (roadmap #99): bounded restarts, quiet success, one alert."""
from core import supervisor as sv


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(check, recover=None, **kw):
    clock, sent, audit = Clock(), [], []
    s = sv.Supervisor([sv.Probe("ollama", check, recover)], clock=clock,
                      notify=lambda m, **k: sent.append(m) or True,
                      audit=lambda *a, **k: audit.append(a[1]), **kw)
    return s, clock, sent, audit


def test_healthy_is_silent():
    s, _, sent, audit = make(lambda: (True, "ok"))
    assert s.run_once() == {"ollama": "ok"}
    assert sent == [] and audit == []


def test_restart_that_works_never_alerts_and_gets_grace():
    state = {"up": False, "restarts": 0}

    def fix():
        state["restarts"] += 1
        state["up"] = True
        return True
    s, clock, sent, audit = make(lambda: (state["up"], "d"), fix, alert_after=1)
    assert s.run_once()["ollama"] == "restarted"
    state["up"] = False                       # still down inside the grace window
    clock.t += 10
    assert s.run_once()["ollama"] == "grace"
    assert state["restarts"] == 1 and sent == []


def test_backoff_and_hourly_cap_then_single_alert():
    calls = []
    s, clock, sent, _ = make(lambda: (False, "down"),
                             lambda: calls.append(clock.t) or False,
                             alert_after=2, max_restarts_per_hour=3)
    results = []
    for _ in range(40):
        results.append(s.run_once()["ollama"])
        clock.t += 31
    assert len(calls) == 3                    # capped, never a restart loop
    assert "backoff" in results and results[-1] == "gave_up"
    assert len(sent) == 1                     # exactly one alert per outage
    assert calls[1] - calls[0] >= 30 and calls[2] - calls[1] >= 60   # doubling backoff


def test_alert_only_probe_alerts_after_threshold_and_notes_recovery():
    up = {"v": False}
    s, clock, sent, audit = make(lambda: (up["v"], "disk full"), None, alert_after=3)
    for _ in range(5):
        s.run_once()
    assert len(sent) == 1 and "disk full" in sent[0]
    up["v"] = True
    s.run_once()
    assert len(sent) == 2 and "healthy again" in sent[1]
    assert "recovered" in audit


def test_raising_probe_counts_as_failure_not_crash():
    def boom():
        raise RuntimeError("x")
    s, *_ = make(boom)
    assert s.run_once() == {"ollama": "failing"}


def test_make_restart_uses_argv_no_shell():
    seen = {}

    class R:
        returncode = 0

    def runner(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return R()
    fn = sv.make_restart("docker restart ollama; rm -rf /", runner)
    assert fn() is True
    assert seen["argv"] == ["docker", "restart", "ollama;", "rm", "-rf", "/"]
    assert "shell" not in seen["kw"]
    assert sv.make_restart("", runner) is None and sv.make_restart(None, runner) is None


def test_disk_and_ram_probes():
    U = type("U", (), {})
    u = U(); u.total, u.free = 100, 3
    assert sv.disk_probe(".", 5, usage=lambda p: u)()[0] is False
    u.free = 50
    assert sv.disk_probe(".", 5, usage=lambda p: u)()[0] is True
    assert sv.ram_probe(300, available=lambda: 100)()[0] is False
    assert sv.ram_probe(300, available=lambda: 900)()[0] is True


def test_http_probe():
    R = type("R", (), {"status_code": 200})
    assert sv.http_probe("o", "http://x", get=lambda *a, **k: R())()[0] is True
    R.status_code = 502
    assert sv.http_probe("o", "http://x", get=lambda *a, **k: R())()[0] is False

    def err(*a, **k):
        raise ConnectionError("no")
    assert sv.http_probe("o", "http://x", get=err)()[0] is False


def test_build_from_config_wires_restart_only_when_configured():
    s = sv.build_from_config({"supervisor_ollama_restart_cmd": "docker restart ollama"})
    by = {p.name: p for p in s.probes}
    assert by["ollama"].recover is not None and by["aether"].recover is None
    assert by["disk"].recover is None
