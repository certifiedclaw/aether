"""Restricted subprocess runner for model-generated Python.

WHAT THIS IS: defence in depth on top of the approval dialog. It is not a security
boundary against code that is actively trying to escape.

WHAT IT ENFORCES
    * scratch working directory, deleted afterwards (and TEMP/TMP point into it)
    * scrubbed environment: allowlist only, so API keys and tokens in the parent's
      environment never reach the code
    * `python -I`: ignores PYTHON* env vars and user site-packages, keeps the cwd and
      script dir off sys.path (a planted json.py is not imported)
    * wall-clock timeout, process-tree kill (POSIX process group; Windows job object)
    * memory cap (POSIX RLIMIT_AS; Windows job memory limit) and CPU-seconds cap (POSIX)
    * output cap
    * network and process spawning blocked by default through a Python audit hook

WHAT IT DOES NOT ENFORCE
    * Filesystem isolation. The code can still read and write anything the user can,
      by absolute path. Real isolation needs a container or an OS sandbox.
    * Network/spawn blocking is an in-process audit hook. ctypes or a C extension that
      opens sockets itself bypasses it. If spawning is allowed but the network is not,
      child processes are NOT network-restricted; run_python() logs that combination.

Config (flat keys, config/config.json):
    sandbox.enabled            true    false = legacy behaviour (plain subprocess, home cwd)
    sandbox.allow_network      false
    sandbox.allow_subprocess   false   needed for pip, os.startfile, launching apps
    sandbox.memory_mb          1024
    sandbox.cpu_seconds        120
    sandbox.timeout_s          120
    sandbox.max_output_chars   20000
    sandbox.env_passthrough    []      extra environment variable names to keep
    sandbox.backend            "process"  or "docker" (real filesystem isolation)
    sandbox.docker_image       "python:3.12-slim"
    sandbox.docker_mounts_ro   []      host folders exposed read-only at /mnt/<name>
    sandbox.docker_mounts_rw   []      host folders exposed read-write at /mnt/<name>

DOCKER BACKEND: the code runs in a throwaway container: no network unless allowed, all
capabilities dropped, read-only root filesystem, pid/memory/cpu limits, and it sees only
/work (the scratch dir) plus the folders you list. It fails CLOSED: if Docker is not
available the run is refused, never silently downgraded to the process backend.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("aether")

_ENV_ALLOWLIST = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "OS",
    "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "USERNAME", "USER", "LOGNAME",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS",
)


@dataclass(frozen=True)
class SandboxPolicy:
    enabled: bool = True
    allow_network: bool = False
    allow_subprocess: bool = False
    memory_mb: int = 1024
    cpu_seconds: int = 120
    timeout_s: int = 120
    max_output_chars: int = 20_000
    env_passthrough: tuple = field(default_factory=tuple)
    backend: str = "process"
    docker_image: str = "python:3.12-slim"
    docker_mounts_ro: tuple = field(default_factory=tuple)
    docker_mounts_rw: tuple = field(default_factory=tuple)

    def mount_map(self) -> list[tuple[str, str, str]]:
        """(host path, container path, 'ro'|'rw'); names de-duplicated so two folders
        called 'Documents' do not collide."""
        out, used = [], set()
        for mode, paths in (("ro", self.docker_mounts_ro), ("rw", self.docker_mounts_rw)):
            for host in paths:
                base = (Path(host).name or "mount").replace(" ", "_")
                name, n = base, 2
                while name in used:
                    name, n = f"{base}{n}", n + 1
                used.add(name)
                out.append((str(host), f"/mnt/{name}", mode))
        return out

    @classmethod
    def from_config(cls, cfg: dict | None) -> "SandboxPolicy":
        cfg = cfg or {}
        d = cls()

        def get(name, default):
            if f"sandbox.{name}" in cfg:
                return cfg[f"sandbox.{name}"]
            nested = cfg.get("sandbox")
            if isinstance(nested, dict) and name in nested:
                return nested[name]
            return default

        def flag(name, default):
            v = get(name, default)
            if isinstance(v, str):
                return v.strip().lower() in ("1", "true", "yes", "on")
            return bool(v)

        def num(name, default, lo, hi):
            try:
                return int(min(max(float(get(name, default)), lo), hi))
            except (TypeError, ValueError):
                return default

        def strs(name):
            v = get(name, [])
            return tuple(str(x) for x in v) if isinstance(v, (list, tuple)) else ()

        backend = str(get("backend", d.backend)).strip().lower()
        if backend not in ("process", "docker"):
            # A typo must not quietly weaken isolation: unknown means the strict one.
            logger.warning("[Sandbox] unknown sandbox.backend %r; using 'docker'", backend)
            backend = "docker"
        image = str(get("docker_image", d.docker_image)).strip() or d.docker_image

        extra = get("env_passthrough", [])
        if not isinstance(extra, (list, tuple)):
            extra = []
        return cls(
            backend=backend, docker_image=image,
            docker_mounts_ro=strs("docker_mounts_ro"), docker_mounts_rw=strs("docker_mounts_rw"),
            enabled=flag("enabled", d.enabled),
            allow_network=flag("allow_network", d.allow_network),
            allow_subprocess=flag("allow_subprocess", d.allow_subprocess),
            memory_mb=num("memory_mb", d.memory_mb, 64, 65_536),
            cpu_seconds=num("cpu_seconds", d.cpu_seconds, 1, 3_600),
            timeout_s=num("timeout_s", d.timeout_s, 1, 3_600),
            max_output_chars=num("max_output_chars", d.max_output_chars, 200, 5_000_000),
            env_passthrough=tuple(str(x) for x in extra),
        )

    def describe(self) -> str:
        """One line for the approval dialog / generation prompt."""
        if not self.enabled:
            return "Sandbox OFF: runs with your full user permissions."
        if self.backend == "docker":
            mounts = ", ".join(f"{h} ({m})" for h, _c, m in self.mount_map()) or "none"
            return ("Container run (Docker): network "
                    + ("allowed" if self.allow_network else "blocked")
                    + f", {self.memory_mb} MB memory, {self.timeout_s}s limit, "
                      f"no access to other files on this PC. Folders shared: {mounts}.")
        parts = [
            "network " + ("allowed" if self.allow_network else "blocked"),
            "process spawning " + ("allowed" if self.allow_subprocess else "blocked"),
            f"{self.memory_mb} MB memory",
            f"{self.timeout_s}s limit",
            "scratch working dir",
            "secrets stripped from environment",
        ]
        return "Restricted run: " + ", ".join(parts) + ". Files elsewhere on this PC are still reachable."


@dataclass
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def scrub_env(policy: SandboxPolicy, scratch: str) -> dict:
    keep = {k.upper() for k in _ENV_ALLOWLIST} | {k.upper() for k in policy.env_passthrough}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env["TEMP"] = env["TMP"] = env["TMPDIR"] = scratch
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONNOUSERSITE"] = "1"
    return env


# The runner executes inside the child BEFORE the generated script. It is written to the
# scratch dir as runner.py. argv: runner.py <script> <memory_mb> <cpu_s> <net:0|1> <spawn:0|1>
_RUNNER = r'''
import runpy, sys

script, mem_mb, cpu_s, allow_net, allow_spawn = (
    sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4] == "1", sys.argv[5] == "1")
sys.argv = [script]


def _limit_posix():
    import resource
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
    except Exception:
        pass
    try:
        b = mem_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (b, b))
    except Exception:
        pass


def _limit_windows():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    H = wintypes.HANDLE

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "r_ops", "w_ops", "o_ops", "r_bytes", "w_bytes", "o_bytes")]

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class EXT(ctypes.Structure):
        _fields_ = [("Basic", BASIC), ("Io", IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32.CreateJobObjectW.restype = H
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.GetCurrentProcess.restype = H
    k32.AssignProcessToJobObject.argtypes = [H, H]
    k32.SetInformationJobObject.argtypes = [H, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]

    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = EXT()
    # JOB_OBJECT_LIMIT_JOB_MEMORY (0x200) | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE (0x2000)
    info.Basic.LimitFlags = 0x200 | 0x2000
    info.JobMemoryLimit = mem_mb * 1024 * 1024
    k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
    k32.AssignProcessToJobObject(job, k32.GetCurrentProcess())
    return job          # keep the handle alive for the life of the process


_job = None
try:
    if sys.platform == "win32":
        _job = _limit_windows()
    else:
        _limit_posix()
except Exception:
    pass

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_NET_EVENTS = {"socket.connect", "socket.bind", "socket.getaddrinfo",
               "socket.gethostbyname", "socket.gethostbyaddr", "socket.sendto"}
_SPAWN_EVENTS = {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn",
                 "os.spawn", "os.startfile", "os.fork", "os.forkpty"}


def _audit(event, args):
    if not allow_net and event in _NET_EVENTS:
        host = None
        if event == "socket.getaddrinfo" and args:
            host = args[0]
        elif event in ("socket.connect", "socket.bind", "socket.sendto") and len(args) > 1:
            addr = args[1]
            host = addr[0] if isinstance(addr, tuple) and addr else None
        if host not in _LOOPBACK:
            raise PermissionError("Blocked by the Aether sandbox: network access is disabled "
                                  "(sandbox.allow_network).")
    if not allow_spawn and event in _SPAWN_EVENTS:
        raise PermissionError("Blocked by the Aether sandbox: starting other programs is "
                              "disabled (sandbox.allow_subprocess).")


sys.addaudithook(_audit)
runpy.run_path(script, run_name="__main__")
'''


def _clip(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[{len(text) - limit} more characters omitted]"


def _kill_tree(proc: "subprocess.Popen") -> None:
    """Kill the child and everything it started. Never signals our own process group."""
    try:
        if os.name == "posix":
            import signal
            os.killpg(proc.pid, signal.SIGKILL)   # pid is the child's own group (new session)
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def docker_available(timeout: float = 8.0) -> tuple[bool, str]:
    """True only when the docker CLI exists AND its daemon answers."""
    exe = shutil.which("docker")
    if not exe:
        return False, "the docker command was not found"
    try:
        r = subprocess.run([exe, "version", "--format", "{{.Server.Version}}"],
                           capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return False, f"could not query Docker ({type(e).__name__})"
    if r.returncode != 0 or not r.stdout.strip():
        return False, "the Docker daemon is not running"
    return True, r.stdout.strip()


def docker_command(policy: SandboxPolicy, name: str, scratch: str, exe: str = "docker") -> list[str]:
    """argv for `docker run`. Pure, so it can be unit-tested without a daemon."""
    cmd = [exe, "run", "--rm", "--name", name,
           "--network", "bridge" if policy.allow_network else "none",
           "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
           "--read-only", "--tmpfs", "/tmp:rw,size=64m",
           "--memory", f"{policy.memory_mb}m", "--memory-swap", f"{policy.memory_mb}m",
           "--cpus", "1", "--pids-limit", "128",
           "--workdir", "/work",
           "--mount", f"type=bind,source={scratch},target=/work"]
    if hasattr(os, "getuid"):
        cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]   # scratch is 0700 owned by us
    for host, inside, mode in policy.mount_map():
        cmd += ["--mount", f"type=bind,source={host},target={inside}"
                           + (",readonly" if mode == "ro" else "")]
    cmd += [policy.docker_image, "python", "-I", "/work/runner.py", "/work/task.py",
            str(policy.memory_mb), str(policy.cpu_seconds),
            "1" if policy.allow_network else "0", "1" if policy.allow_subprocess else "0"]
    return cmd


def _run_in_docker(policy: SandboxPolicy, scratch: str) -> SandboxResult:
    ok, info = docker_available()
    if not ok:
        return SandboxResult(-2, "", "Docker sandbox unavailable: " + info +
                             ". The code was not run. Start Docker, or set "
                             "sandbox.backend to \"process\" to use the weaker sandbox.")
    for host, _inside, _mode in policy.mount_map():
        if not Path(host).is_dir():
            return SandboxResult(-2, "", f"Docker sandbox: shared folder does not exist: {host}. "
                                         "The code was not run.")
    name = f"aether-sbx-{uuid.uuid4().hex[:12]}"
    cmd = docker_command(policy, name, scratch, exe=shutil.which("docker") or "docker")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    try:
        out, err = proc.communicate(timeout=policy.timeout_s)
    except subprocess.TimeoutExpired:
        # Killing the CLI does not stop the container; kill it by name.
        try:
            subprocess.run([shutil.which("docker") or "docker", "kill", name],
                           capture_output=True, timeout=15)
        except Exception:
            pass
        _kill_tree(proc)
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        return SandboxResult(-1, "", "", timed_out=True)
    return SandboxResult(proc.returncode, _clip(out, policy.max_output_chars),
                         _clip(err, policy.max_output_chars))


def run_python(code: str, policy: SandboxPolicy | None = None) -> SandboxResult:
    """Run `code` under `policy`. Callers must have obtained approval first."""
    policy = policy or SandboxPolicy()

    if not policy.enabled:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False,
                                         encoding="utf-8") as f:
            f.write(code)
            path = f.name
        try:
            r = subprocess.run([sys.executable, path], capture_output=True, text=True,
                               timeout=policy.timeout_s, cwd=str(Path.home()))
            return SandboxResult(r.returncode, r.stdout or "", r.stderr or "")
        except subprocess.TimeoutExpired:
            return SandboxResult(-1, "", "", timed_out=True)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    if policy.allow_subprocess and not policy.allow_network:
        logger.warning("[Sandbox] process spawning is allowed while the network is blocked: "
                       "child processes are not network-restricted.")

    scratch = tempfile.mkdtemp(prefix="aether-sandbox-")
    try:
        script = os.path.join(scratch, "task.py")
        runner = os.path.join(scratch, "runner.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(code)
        with open(runner, "w", encoding="utf-8") as f:
            f.write(_RUNNER)

        if policy.backend == "docker":
            return _run_in_docker(policy, scratch)

        cmd = [sys.executable, "-I", runner, script, str(policy.memory_mb),
               str(policy.cpu_seconds), "1" if policy.allow_network else "0",
               "1" if policy.allow_subprocess else "0"]
        popen_kwargs: dict = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  encoding="utf-8", errors="replace", cwd=scratch,
                                  env=scrub_env(policy, scratch))
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True   # own process group => pgid == pid

        proc = subprocess.Popen(cmd, **popen_kwargs)
        try:
            out, err = proc.communicate(timeout=policy.timeout_s)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            return SandboxResult(-1, "", "", timed_out=True)

        return SandboxResult(proc.returncode,
                             _clip(out, policy.max_output_chars),
                             _clip(err, policy.max_output_chars))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
