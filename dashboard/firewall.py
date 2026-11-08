"""OS-firewall setup so the dashboard is reachable on the LAN / Tailscale.

Best-effort and side-effecting (netsh / socketfilterfw / firewall-cmd / iptables); run it
off the event loop. The Windows command list is a pure function so it can be tested
without Windows. Moved out of dashboard/server.py unchanged.
"""
from __future__ import annotations

def ensure_network_access(port: int) -> None:
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


# Remote addresses the dashboard firewall rule admits: the local subnet and Tailscale's
# CGNAT range. Never "any".
_FW_REMOTE_SCOPE = "LocalSubnet,100.64.0.0/10"
_FW_PROFILES     = "private,domain"      # never Public


def _windows_firewall_commands(port: int, scoped_exists: bool,
                               legacy_port_exists: bool, legacy_prog_exists: bool) -> list[str]:
    """netsh commands for the dashboard. Pure so it can be tested without Windows.

    One inbound rule for this port only, limited to Private/Domain profiles and to the
    local subnet / Tailscale. It never changes a network's category and never opens a
    program-wide rule. Older unscoped rules created by earlier versions are removed."""
    cmds: list[str] = []
    if legacy_port_exists:
        cmds.append(f'netsh advfirewall firewall delete rule name="AETHER Dashboard Port {port}"')
    if legacy_prog_exists:
        cmds.append('netsh advfirewall firewall delete rule name="AETHER Dashboard Python"')
    if not scoped_exists:
        cmds.append(
            f'netsh advfirewall firewall add rule name="AETHER Dashboard Port {port} (scoped)" '
            f'protocol=TCP dir=in localport={port} action=allow '
            f'profile={_FW_PROFILES} remoteip={_FW_REMOTE_SCOPE}'
        )
    return cmds


def _ensure_network_access_windows(port: int) -> None:
    import sys, subprocess, os, tempfile, threading
    import ctypes, time

    scoped_rule = f"AETHER Dashboard Port {port} (scoped)"
    legacy_port = f"AETHER Dashboard Port {port}"
    legacy_prog = "AETHER Dashboard Python"

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

    bat_lines = ["@echo off"] + _windows_firewall_commands(
        port,
        scoped_exists=_netsh_rule_exists(scoped_rule),
        legacy_port_exists=_netsh_rule_exists(legacy_port),
        legacy_prog_exists=_netsh_rule_exists(legacy_prog),
    )
    if _network_is_public():
        # Reported, never changed: reclassifying a network is the user's decision.
        print("[Dashboard] A connected network is classed Public; the dashboard rule only applies "
              "to Private/Domain networks. Change that network's category in Windows settings "
              "if you want phone access on it.")
    if len(bat_lines) == 1:
        return  # already configured

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
