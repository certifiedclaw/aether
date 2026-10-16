from core.resource_governor import ResourceGovernor, _MIN_GAP_S


class Rig:
    def __init__(self, cfg=None):
        self.t = 1000.0
        self.cpu = 10.0
        self.ram = 8000.0
        self.cfg = cfg or {}
        self.gov = ResourceGovernor(lambda: self.cfg, lambda: (self.cpu, self.ram), lambda: self.t)

    def tick(self, n=1):
        for _ in range(n):
            self.t += _MIN_GAP_S + 0.1
            p = self.gov.pressure()
        return p


def test_idle_host_allows_background():
    r = Rig()
    assert r.tick(3).busy is False
    assert r.gov.allow_background() is True


def test_sustained_cpu_marks_busy_but_a_spike_does_not():
    r = Rig()
    r.tick(5)
    r.cpu = 100.0
    assert r.tick(1).busy is False          # one spike averaged with calm samples
    assert r.tick(5).busy is True           # sustained
    assert r.gov.allow_background() is False
    assert r.gov.deferred == 1


def test_hysteresis_resumes_only_after_real_drop():
    r = Rig()
    r.cpu = 95.0
    assert r.tick(6).busy is True
    r.cpu = 80.0                             # below 85 but above 75: stay busy
    assert r.tick(6).busy is True
    r.cpu = 20.0
    assert r.tick(6).busy is False


def test_low_free_ram_marks_busy():
    r = Rig()
    r.ram = 500.0
    p = r.tick(1)
    assert p.busy and "RAM" in p.reason
    r.ram = 1600.0                           # above floor but inside hysteresis band (1800)
    assert r.tick(1).busy is True
    r.ram = 4000.0
    assert r.tick(1).busy is False


def test_deferred_job_is_forced_after_max_wait():
    r = Rig({"governor_max_defer_s": 100})
    r.cpu = 99.0
    r.tick(6)
    assert r.gov.allow_background(waiting_s=10) is False
    assert r.gov.allow_background(waiting_s=100) is True
    assert r.gov.forced == 1


def test_disabled_always_allows():
    r = Rig({"governor_enabled": False})
    r.cpu = 100.0
    r.tick(6)
    assert r.gov.allow_background() is True
    assert "disabled" in r.gov.status()
    assert Rig({"governor_enabled": "no"}).gov.enabled() is False


def test_thresholds_are_configurable_and_junk_is_ignored():
    r = Rig({"governor_cpu_busy": 50, "governor_ram_min_free_mb": "junk"})
    r.cpu = 60.0
    assert r.tick(6).busy is True
    r2 = Rig({"governor_cpu_busy": -5})
    r2.cpu = 90.0
    assert r2.tick(6).busy is True           # negative -> default 85


def test_broken_sensor_never_blocks_work():
    def boom():
        raise OSError("no /proc")
    g = ResourceGovernor(lambda: {}, boom, lambda: 5000.0)
    assert g.allow_background() is True


def test_status_text_reports_state():
    r = Rig()
    r.cpu = 99.0
    r.tick(6)
    assert "BUSY" in r.gov.status() and "CPU" in r.gov.status()


def test_scheduler_defers_then_runs(monkeypatch):
    from core import resource_governor as rg
    from core.task_scheduler import TaskScheduler, ScheduledTask
    r = Rig()
    r.cpu = 99.0
    r.tick(6)
    monkeypatch.setattr(rg, "_instance", r.gov)
    ran = []
    task = ScheduledTask("t", lambda: ran.append(1), 1.0)
    assert TaskScheduler._governor_allows(task, 50.0) is False     # busy: held back
    assert task.deferred_since == 50.0
    r.cpu = 5.0
    r.tick(8)
    assert TaskScheduler._governor_allows(task, 60.0) is True      # clear: runs
