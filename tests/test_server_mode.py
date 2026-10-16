import core.installer as inst


def test_default_allows_install(monkeypatch):
    monkeypatch.delenv("AETHER_SKIP_INSTALL", raising=False)
    monkeypatch.delenv("AETHER_HEADLESS", raising=False)
    assert inst.installs_disabled() is False


def test_headless_disables(monkeypatch):
    monkeypatch.delenv("AETHER_SKIP_INSTALL", raising=False)
    monkeypatch.setenv("AETHER_HEADLESS", "1")
    assert inst.installs_disabled() is True


def test_explicit_override(monkeypatch):
    monkeypatch.setenv("AETHER_HEADLESS", "1")
    monkeypatch.setenv("AETHER_SKIP_INSTALL", "0")
    assert inst.installs_disabled() is False
    monkeypatch.setenv("AETHER_HEADLESS", "")
    monkeypatch.setenv("AETHER_SKIP_INSTALL", "1")
    assert inst.installs_disabled() is True


def test_install_for_config_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("AETHER_SKIP_INSTALL", "1")
    calls = []
    monkeypatch.setattr(inst, "_pip", lambda *a, **k: calls.append(a) or True)
    monkeypatch.setattr(inst.subprocess, "run", lambda *a, **k: calls.append(a))
    logs = []
    inst.install_for_config({}, log=logs.append)
    assert calls == [] and logs


def test_headless_ui_covers_core_ui_surface():
    """main.py / core call these on the UI object; HeadlessUI must provide each
    (a missing one crashed headless boot with AttributeError)."""
    from core.headless_ui import HeadlessUI
    ui = HeadlessUI()
    for name in ("write_log", "set_state", "clear_log", "confirm_blocking",
                 "update_message_stats", "show_startup_panel", "mark_startup_ready",
                 "set_startup_status", "hide_startup_panel", "wait_for_api_key",
                 "set_chat_sessions", "on_text_command", "on_stop_command",
                 "on_reconfigure", "muted", "current_file", "root"):
        assert hasattr(ui, name), name
    ui.set_chat_sessions(["a", "b"], "a")
    ui.clear_log()


def test_server_files_present_and_secrets_excluded():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for f in ("Dockerfile", "docker-compose.yml", "docker/entrypoint.sh", "requirements-server.txt"):
        assert (root / f).exists(), f
    ign = (root / ".dockerignore").read_text()
    assert "config/api_keys.json" in ign and "config/certs" in ign and ".env" in ign
    assert "/login" in (root / "Dockerfile").read_text()
