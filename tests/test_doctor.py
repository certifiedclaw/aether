"""core/doctor.py: every check against a throwaway install; no network, no Ollama."""
import hashlib
import json
import os
import stat

import pytest

from core import data_home, doctor as d


@pytest.fixture
def root(tmp_path, monkeypatch):
    for sub in ("config", "logs", "data", "memory", "plugins"):
        (tmp_path / sub).mkdir()
    monkeypatch.setattr(data_home, "APP_ROOT", tmp_path)
    monkeypatch.setenv("AETHER_DATA_DIR", str(tmp_path / "user-data"))
    data_home.reset_for_tests()
    monkeypatch.setattr(d, "_http_json", lambda url, timeout=4.0: None)
    monkeypatch.setattr(d, "_ram", lambda: (16.0, 32.0))
    monkeypatch.setattr(d, "_git", lambda *a: None)
    yield tmp_path
    data_home.reset_for_tests()


def _cfg(root, obj):
    p = root / "config" / "api_keys.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def by_name(findings, name):
    return [f for f in findings if f.name == name]


# ── config ───────────────────────────────────────────────────────────────────
def test_missing_config_warns_not_fails(root):
    cfg, f = d._load_cfg(root)
    assert cfg == {} and f.status == d.WARN and "missing" in f.detail


def test_corrupt_config_fails_and_mentions_backup(root):
    p = root / "config" / "api_keys.json"
    p.write_text("{not json", encoding="utf-8")
    (root / "config" / "api_keys.json.corrupt").write_text("x")
    cfg, f = d._load_cfg(root)
    assert cfg == {} and f.status == d.FAIL and ".corrupt" in f.detail


def test_non_object_config_fails(root):
    _cfg(root, [1, 2])
    assert d._load_cfg(root)[1].status == d.FAIL


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_config_permissions_detected_and_fixed(root):
    p = _cfg(root, {})
    p.chmod(0o666)
    f = d.check_config_permissions(root)
    assert f.status == d.WARN and f.auto == "chmod_config"
    msgs = d.apply_fixes([f], root)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600 and "600" in msgs[0]
    assert d.check_config_permissions(root).status == d.OK


# ── secrets ──────────────────────────────────────────────────────────────────
def test_secret_storage_names_keys_but_never_values(root, monkeypatch):
    for n in ("GEMINI_API_KEY", "AETHER_GEMINI_API_KEY"):
        monkeypatch.delenv(n, raising=False)
    cfg = {"gemini_api_key": "SUPER-SECRET-VALUE-123"}
    f = d.check_secret_storage(root, cfg)
    if f is None:                      # credentials module unavailable in this env
        pytest.skip("credentials layer not importable")
    assert "SUPER-SECRET-VALUE-123" not in f.detail + f.fix
    if f.status == d.WARN:
        assert "gemini_api_key" in f.detail


def test_report_never_contains_secret_values(root, monkeypatch):
    _cfg(root, {"gemini_api_key": "SUPER-SECRET-VALUE-123", "llm_provider": "gemini"})
    text = d.render(d.run_checks(root))
    assert "SUPER-SECRET-VALUE-123" not in text


def test_git_tracked_secret_is_a_failure(root, monkeypatch):
    monkeypatch.setattr(d, "_git", lambda r, *a: 0 if a[0] == "ls-files" else 1)
    f = d.check_git_secrets(root)[0]
    assert f.status == d.FAIL and "rotate" in f.fix


def test_git_untracked_but_not_ignored_warns(root, monkeypatch):
    monkeypatch.setattr(d, "_git", lambda r, *a: 1)
    assert d.check_git_secrets(root)[0].status == d.WARN


def test_git_ignored_is_ok(root, monkeypatch):
    monkeypatch.setattr(d, "_git", lambda r, *a: 1 if a[0] == "ls-files" else 0)
    assert d.check_git_secrets(root)[0].status == d.OK


def test_no_git_means_no_finding(root):
    assert d.check_git_secrets(root) == []


# ── folders / disk / memory files ────────────────────────────────────────────
def test_missing_dirs_warn_and_autofix(root):
    (root / "logs").rmdir()
    f = d.check_dirs(root)
    assert f.status == d.WARN and f.auto == "make_dirs"
    d.apply_fixes([f], root)
    assert (root / "logs").is_dir() and d.check_dirs(root).status == d.OK


def test_disk_thresholds(root, monkeypatch):
    import collections
    U = collections.namedtuple("U", "total used free")
    for free_gb, want in ((50, d.OK), (3, d.WARN), (0.2, d.FAIL)):
        monkeypatch.setattr(d.shutil, "disk_usage", lambda p, g=free_gb: U(0, 0, int(g * 2**30)))
        assert d.check_disk(root)[0].status == want


def test_memory_db_ok_and_corrupt(root):
    import sqlite3
    db = data_home.data_dir() / "memory.db"
    con = sqlite3.connect(db)
    con.execute("create table t(x)")
    con.commit()
    con.close()
    assert by_name(d.check_memory_files(root), "Memory database")[0].status == d.OK
    db.write_bytes(b"this is not a sqlite database at all" * 50)
    assert by_name(d.check_memory_files(root), "Memory database")[0].status == d.FAIL


def test_long_term_json_validated(root):
    lt = data_home.long_term_path()
    lt.write_text("{}")
    assert by_name(d.check_memory_files(root), "Long-term memory file")[0].status == d.OK
    lt.write_text("{broken")
    assert by_name(d.check_memory_files(root), "Long-term memory file")[0].status == d.FAIL


def test_data_location_and_stale_legacy_warning(root):
    legacy = root / "data" / "memory.db"
    legacy.write_bytes(b"old")
    findings = d.check_data_home(root)
    location = by_name(findings, "User data location")[0]
    assert location.status == d.OK
    assert location.detail == str(root / "user-data")

    legacy.write_bytes(b"changed")
    warning = by_name(d.check_data_home(root), "Legacy data copy")[0]
    assert warning.status == d.WARN


# ── LLM ──────────────────────────────────────────────────────────────────────
def _ollama(monkeypatch, models, loaded=()):
    def fake(url, timeout=4.0):
        if url.endswith("/api/tags"):
            return {"models": [{"name": n, "size": int(gb * 2**30)} for n, gb in models]}
        if url.endswith("/api/ps"):
            return {"models": list(loaded)}
        return None
    monkeypatch.setattr(d, "_http_json", fake)


def test_unreachable_ollama_fails(root):
    f = d.check_llm({})[0]
    assert f.status == d.FAIL and "Start Ollama" in f.fix


def test_model_installed_ok(root, monkeypatch):
    _ollama(monkeypatch, [("qwen3:8b", 5.0)])
    out = d.check_llm({"llm_model": "qwen3:8b"})
    assert by_name(out, "Model")[0].status == d.OK


def test_latest_tag_is_implied(root, monkeypatch):
    _ollama(monkeypatch, [("llama3.2:latest", 2.0)])
    assert by_name(d.check_llm({"llm_model": "llama3.2"}), "Model")[0].status == d.OK


def test_missing_model_suggests_installed_variant_or_pull(root, monkeypatch):
    _ollama(monkeypatch, [("llama3.2:1b", 1.0)])
    f = by_name(d.check_llm({"llm_model": "llama3.2"}), "Model")[0]
    assert f.status == d.FAIL and "llama3.2:1b" in f.fix
    f2 = by_name(d.check_llm({"llm_model": "qwen3:8b"}), "Model")[0]
    assert "ollama pull qwen3:8b" in f2.fix


def test_model_bigger_than_free_ram_warns(root, monkeypatch):
    _ollama(monkeypatch, [("big:70b", 40.0)])
    monkeypatch.setattr(d, "_ram", lambda: (8.0, 32.0))
    assert by_name(d.check_llm({"llm_model": "big:70b"}), "Model fits memory")


def test_partial_gpu_offload_warns(root, monkeypatch):
    _ollama(monkeypatch, [("qwen3:8b", 5.0)], loaded=[{"name": "qwen3:8b", "size": 100, "size_vram": 40}])
    assert by_name(d.check_llm({"llm_model": "qwen3:8b"}), "GPU offload")[0].status == d.WARN


def test_cloud_provider_checks_key_not_ollama(root, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    f = d.check_llm({"llm_provider": "gemini"})[0]
    assert f.name == "LLM provider" and "leave this machine" in f.detail


def test_remote_pc_optional_and_checked(root, monkeypatch):
    assert d.check_remote({}) is None
    assert d.check_remote({"llm_remote_url": "http://10.0.0.9:11434"}).status == d.WARN
    _ollama(monkeypatch, [])
    assert d.check_remote({"llm_remote_url": "http://10.0.0.9:11434"}).status == d.OK


# ── dashboard / plugins / resources ──────────────────────────────────────────
def test_dashboard_exposure(root):
    assert d.check_dashboard(root, {}).status == d.OK
    assert d.check_dashboard(root, {"AETHER_DASHBOARD_HOST": "0.0.0.0"}).status == d.FAIL
    (root / "config" / "certs").mkdir()
    (root / "config" / "certs" / "aether.key").write_text("k")
    (root / "config" / "certs" / "aether.crt").write_text("c")
    assert d.check_dashboard(root, {"AETHER_DASHBOARD_HOST": "0.0.0.0"}).status == d.OK


def test_plugins_untrusted_then_trusted_and_underscore_files_ignored(root):
    (root / "plugins" / "_template.py").write_text("x = 1")
    assert d.check_plugins(root, {}) is None
    src = b"def run(parameters, player=None, session_memory=None):\n    return 'hi'\n"
    (root / "plugins" / "hello.py").write_bytes(src)
    f = d.check_plugins(root, {})
    digest = hashlib.sha256(src).hexdigest()
    assert f.status == d.WARN and digest in f.detail
    assert d.check_plugins(root, {"plugins": {"trusted_hashes": {"hello.py": digest}}}).status == d.OK
    assert d.check_plugins(root, {"plugins.trusted_hashes": {"hello.py": digest}}).status == d.OK
    (root / "plugins" / "hello.py").write_bytes(src + b"# edited\n")
    assert d.check_plugins(root, {"plugins": {"trusted_hashes": {"hello.py": digest}}}).status == d.WARN


# ── whole run / rendering / CLI ──────────────────────────────────────────────
def test_run_checks_never_raises_on_an_empty_install(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "_http_json", lambda *a, **k: None)
    monkeypatch.setattr(d, "_git", lambda *a: None)
    found = d.run_checks(tmp_path)               # nothing exists at all
    assert found and all(f.status in (d.OK, d.WARN, d.FAIL) for f in found)


def test_render_orders_failures_first_and_counts(root):
    text = d.render([d.Finding("a", d.OK, "fine"), d.Finding("b", d.FAIL, "bad", "do x"),
                     d.Finding("c", d.WARN, "meh", "do y", auto="make_dirs")], fixed=["created folder(s): logs"])
    assert text.index("[FAIL]") < text.index("[WARN]") < text.index("[ OK ]")
    assert "1 ok, 1 warning(s), 1 failure(s)" in text and "/doctor fix" in text and "Applied:" in text


def test_only_unresolved_findings_trigger_autofix(root):
    ok = d.Finding("x", d.OK, "fine", auto="make_dirs")
    (root / "logs").rmdir()
    assert d.apply_fixes([ok], root) == [] and not (root / "logs").exists()


def test_autofix_errors_are_reported_not_raised(root, monkeypatch):
    monkeypatch.setitem(d.AUTO_FIXES, "boom", lambda r: (_ for _ in ()).throw(OSError("denied")))
    out = d.apply_fixes([d.Finding("x", d.WARN, "w", auto="boom")], root)
    assert "could not apply boom" in out[0]


def test_cli_exit_code_and_json(root, monkeypatch, capsys):
    monkeypatch.setattr(d, "ROOT", root)
    monkeypatch.setattr(d, "run_checks", lambda r=root: [d.Finding("x", d.FAIL, "bad")])
    assert d.main(["--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["findings"][0]["status"] == "FAIL"
    monkeypatch.setattr(d, "run_checks", lambda r=root: [d.Finding("x", d.OK, "fine")])
    assert d.main([]) == 0


def test_chat_command_is_wired_and_documented():
    import pathlib
    src = (pathlib.Path(d.__file__).parent / "chat_commands.py").read_text(encoding="utf-8")
    assert '"/doctor"' in src and "/doctor fix" in src and "from core import doctor" in src
