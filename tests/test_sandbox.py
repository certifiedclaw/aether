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
    for _ in range(20):                       # allow the kernel a moment to reap
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(pid, 9)
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
