"""core/sandbox.py: these tests actually execute code in the child process."""
import os
import sys
import time
from pathlib import Path

import pytest

from core.sandbox import SandboxPolicy, run_python

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX resource limits")


def test_output_and_exit_code():
    r = run_python("print(6*7)")
    assert (r.returncode, r.stdout.strip(), r.timed_out) == (0, "42", False)


def test_nonzero_exit_and_stderr_are_reported():
    r = run_python("import sys; sys.stderr.write('bad'); sys.exit(3)")
    assert r.returncode == 3 and "bad" in r.stderr


def test_runs_in_scratch_dir_that_is_removed():
    r = run_python("import os; print(os.getcwd())")
    cwd = Path(r.stdout.strip())
    assert cwd.name.startswith("aether-sandbox-")
    assert cwd != Path.home()
    assert not cwd.exists()


def test_secrets_in_parent_environment_are_not_visible(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("AETHER_TOKEN", "tok-should-not-leak")
    r = run_python("import os; print(sorted(k for k in os.environ if 'KEY' in k or 'TOKEN' in k))")
    assert r.stdout.strip() == "[]"


def test_env_passthrough_is_opt_in(monkeypatch):
    monkeypatch.setenv("MY_ALLOWED_VAR", "yes")
    code = "import os; print(os.environ.get('MY_ALLOWED_VAR'))"
    assert run_python(code).stdout.strip() == "None"
    assert run_python(code, SandboxPolicy(env_passthrough=("MY_ALLOWED_VAR",))).stdout.strip() == "yes"


NET = ("import socket\n"
       "s = socket.socket()\n"
       "s.settimeout(1)\n"
       "s.connect(('93.184.216.34', 80))\n")


def test_network_is_blocked_by_default():
    r = run_python(NET)
    assert r.returncode != 0 and "Blocked by the Aether sandbox" in r.stderr


def test_dns_lookup_is_blocked_by_default():
    r = run_python("import socket; socket.getaddrinfo('example.com', 80)")
    assert r.returncode != 0 and "Blocked by the Aether sandbox" in r.stderr


def test_loopback_still_works_when_network_is_blocked():
    r = run_python("import socket\ns = socket.socket()\ns.bind(('127.0.0.1', 0))\nprint('ok')")
    assert r.returncode == 0 and r.stdout.strip() == "ok"


def test_network_allowed_when_enabled():
    r = run_python(NET, SandboxPolicy(allow_network=True))
    assert "Blocked by the Aether sandbox" not in r.stderr      # may fail to connect, but not blocked


SPAWN = "import subprocess, sys; print(subprocess.run([sys.executable, '-c', 'print(1)'], capture_output=True, text=True).stdout.strip())"


def test_spawning_programs_is_blocked_by_default():
    r = run_python(SPAWN)
    assert r.returncode != 0 and "Blocked by the Aether sandbox" in r.stderr


def test_spawning_allowed_when_enabled():
    r = run_python(SPAWN, SandboxPolicy(allow_subprocess=True))
    assert r.returncode == 0 and r.stdout.strip() == "1"


def _alive(pid: int) -> bool:
    """Running, not merely a zombie waiting for a parent that never reaps it."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        return state != "Z"
    except OSError:
        return True


@posix_only
def test_timeout_kills_the_whole_process_tree(tmp_path):
    pidfile = tmp_path / "child.pid"
    code = (
        "import subprocess, sys, time\n"
        f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open(r'{pidfile}', 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    t0 = time.monotonic()
    r = run_python(code, SandboxPolicy(allow_subprocess=True, timeout_s=2))
    assert r.timed_out and time.monotonic() - t0 < 15
    pid = int(pidfile.read_text())
    for _ in range(30):                       # allow the kernel a moment to reap
        if not _alive(pid):
            break
        time.sleep(0.1)
    else:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
        pytest.fail("grandchild survived the timeout")


@posix_only
def test_memory_limit_stops_runaway_allocation():
    r = run_python("x = bytearray(2 * 1024 ** 3); print('allocated')",
                   SandboxPolicy(memory_mb=256))
    assert r.returncode != 0 and "allocated" not in r.stdout


def test_output_is_capped():
    r = run_python("print('x' * 100000)", SandboxPolicy(max_output_chars=500))
    assert len(r.stdout) < 700 and "omitted" in r.stdout


def test_disabled_sandbox_is_the_legacy_plain_run():
    r = run_python("import os; print(os.getcwd())", SandboxPolicy(enabled=False))
    assert Path(r.stdout.strip()) == Path.home()


# ── policy parsing ───────────────────────────────────────────────────────────
def test_policy_defaults_are_strict():
    p = SandboxPolicy.from_config({})
    assert p.enabled and not p.allow_network and not p.allow_subprocess


def test_policy_reads_flat_and_nested_keys_and_clamps_junk():
    p = SandboxPolicy.from_config({"sandbox.allow_network": "true", "sandbox.memory_mb": "junk",
                                   "sandbox": {"allow_subprocess": True, "timeout_s": 99999}})
    assert p.allow_network and p.allow_subprocess
    assert p.memory_mb == SandboxPolicy().memory_mb
    assert p.timeout_s == 3600


def test_describe_states_what_is_and_is_not_restricted():
    text = SandboxPolicy().describe()
    assert "network blocked" in text and "still reachable" in text
    assert "OFF" in SandboxPolicy(enabled=False).describe()


# ── Docker backend (command construction and fail-closed logic need no daemon) ─
from core import sandbox as _sb


def _docker(**kw):
    return SandboxPolicy(backend="docker", **kw)


def test_docker_command_is_locked_down_by_default(tmp_path):
    cmd = _sb.docker_command(_docker(), "aether-sbx-x", str(tmp_path))
    joined = " ".join(cmd)
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert "--network none" in joined
    assert "--cap-drop ALL" in joined and "no-new-privileges" in joined
    assert "--read-only" in cmd and "--pids-limit 128" in joined
    assert "--memory 1024m" in joined and "--memory-swap 1024m" in joined
    assert f"source={tmp_path},target=/work" in joined
    assert "target=/mnt" not in joined                    # nothing else from the host
    assert cmd[-9] == "python:3.12-slim"
    assert cmd[-8:-3] == ["python", "-I", "/work/runner.py", "/work/task.py", "1024"]


def test_docker_network_only_when_allowed(tmp_path):
    cmd = _sb.docker_command(_docker(allow_network=True), "n", str(tmp_path))
    assert "--network bridge" in " ".join(cmd) and "--network none" not in " ".join(cmd)


def test_docker_mounts_are_explicit_readonly_or_rw(tmp_path):
    ro, rw = tmp_path / "Documents", tmp_path / "Out"
    ro.mkdir(); rw.mkdir()
    p = _docker(docker_mounts_ro=(str(ro),), docker_mounts_rw=(str(rw),))
    cmd = _sb.docker_command(p, "n", str(tmp_path))
    assert f"type=bind,source={ro},target=/mnt/Documents,readonly" in cmd
    assert f"type=bind,source={rw},target=/mnt/Out" in cmd           # no readonly flag
    assert "readonly" not in [c for c in cmd if c.endswith("/mnt/Out")][0]


def test_mount_names_do_not_collide(tmp_path):
    a, b = tmp_path / "a" / "Docs", tmp_path / "b" / "Docs"
    p = _docker(docker_mounts_ro=(str(a), str(b)))
    assert [c for _h, c, _m in p.mount_map()] == ["/mnt/Docs", "/mnt/Docs2"]


def _no_host_python(monkeypatch):
    monkeypatch.setattr(_sb.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("nothing may be launched"))


def test_docker_backend_fails_closed_when_docker_is_unavailable(monkeypatch):
    monkeypatch.setattr(_sb, "docker_available", lambda *a, **k: (False, "the Docker daemon is not running"))
    _no_host_python(monkeypatch)
    r = run_python("print('must not run')", _docker())
    assert r.returncode == -2 and "was not run" in r.stderr and "daemon" in r.stderr
    assert r.stdout == ""                                  # and it did NOT fall back to the process backend


def test_docker_backend_refuses_a_missing_shared_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(_sb, "docker_available", lambda *a, **k: (True, "29"))
    _no_host_python(monkeypatch)
    r = run_python("print(1)", _docker(docker_mounts_ro=(str(tmp_path / "nope"),)))
    assert r.returncode == -2 and "does not exist" in r.stderr


class _FakeProc:
    def __init__(self, out="42\n", err="", rc=0, hang=False):
        self.returncode, self._out, self._err, self._hang, self.pid = rc, out, err, hang, 4242

    def communicate(self, timeout=None):
        if self._hang and not getattr(self, "_killed", False):
            self._killed = True
            raise _sb.subprocess.TimeoutExpired("docker", timeout)
        return self._out, self._err

    def kill(self):
        pass


def test_docker_backend_runs_and_returns_output(monkeypatch):
    seen = {}
    monkeypatch.setattr(_sb, "docker_available", lambda *a, **k: (True, "29"))
    monkeypatch.setattr(_sb.subprocess, "Popen",
                        lambda cmd, **k: seen.setdefault("cmd", cmd) and _FakeProc())
    r = run_python("print(6*7)", _docker())
    assert (r.returncode, r.stdout.strip()) == (0, "42")
    assert "--network" in seen["cmd"] and "none" in seen["cmd"]


def test_docker_timeout_kills_the_container_by_name(monkeypatch):
    calls = []
    monkeypatch.setattr(_sb, "docker_available", lambda *a, **k: (True, "29"))
    monkeypatch.setattr(_sb.subprocess, "Popen", lambda cmd, **k: _FakeProc(hang=True))
    monkeypatch.setattr(_sb.subprocess, "run",
                        lambda cmd, **k: calls.append(cmd) or _sb.subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(_sb, "_kill_tree", lambda proc: None)
    r = run_python("import time; time.sleep(99)", _docker(timeout_s=1))
    assert r.timed_out
    kill = [c for c in calls if len(c) >= 3 and c[1] == "kill"]
    assert kill and kill[0][2].startswith("aether-sbx-")


def test_unknown_backend_means_docker_not_weaker():
    assert SandboxPolicy.from_config({"sandbox.backend": "dokcer"}).backend == "docker"
    assert SandboxPolicy.from_config({}).backend == "process"
    assert SandboxPolicy.from_config({"sandbox.backend": " Docker "}).backend == "docker"


def test_docker_describe_says_what_is_shared(tmp_path):
    text = _docker(docker_mounts_ro=(str(tmp_path),)).describe()
    assert "Docker" in text and "network blocked" in text and str(tmp_path) in text
    assert "no access to other files" in text


def test_generation_prompt_describes_the_container_not_host_paths(monkeypatch, tmp_path):
    import agent.executor as ex
    seen = {}
    pol = _docker(docker_mounts_ro=(str(tmp_path),))
    monkeypatch.setattr(ex, "_sandbox_policy", lambda: pol)
    monkeypatch.setattr(ex, "call_llm_text",
                        lambda prompt, system="", **k: seen.setdefault("system", system) and "print(1)")
    import core.safety as safety
    monkeypatch.setattr(safety, "confirm_action", lambda *a, **k: False)
    with pytest.raises(ex.GeneratedCodeNotRun):
        ex._run_generated_code("task")
    assert "container" in seen["system"].lower() and "/work" in seen["system"]
    assert "Desktop" not in seen["system"]


_docker_ok = _sb.docker_available()[0]


@pytest.mark.skipif(not _docker_ok, reason="no reachable Docker daemon")
def test_real_container_isolation(tmp_path):
    secret = tmp_path / "host-secret.txt"
    secret.write_text("top secret")
    code = ("import os, socket\n"
            f"print(os.path.exists(r'{secret}'))\n"
            "try:\n"
            "    socket.create_connection(('1.1.1.1', 53), timeout=2); print('net')\n"
            "except OSError:\n"
            "    print('nonet')\n")
    r = run_python(code, _docker(timeout_s=120))
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["False", "nonet"]
