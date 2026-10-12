"""core/credentials.py + tools/secrets_cli.py: env > OS keyring > config file, never logged,
never fatal, and the migration cannot lose a key."""
import json
import logging
import sys
import types

import pytest

from core import credentials as cred
from tools import secrets_cli

KEY = "AIzaFAKE-gemini-key-1234567890"
EL = "el-FAKE-key-abcdef"


class FakeKeyring(types.ModuleType):
    """Stands in for the `keyring` package."""
    def __init__(self, store=None, fail=None, corrupt=False):
        super().__init__("keyring")
        self.store = dict(store or {})
        self.fail, self.corrupt, self.gets = fail, corrupt, 0

    def get_password(self, service, name):
        self.gets += 1
        if self.fail == "get":
            raise RuntimeError("backend exploded")
        return self.store.get((service, name))

    def set_password(self, service, name, value):
        if self.fail == "set":
            raise RuntimeError("locked")
        self.store[(service, name)] = "garbled" if self.corrupt else value

    def delete_password(self, service, name):
        self.store.pop((service, name), None)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    cred.clear_cache()
    for n in cred.SECRET_NAMES:
        for var in cred._env_names(n):
            monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cred, "_warned", False)
    yield
    cred.clear_cache()


@pytest.fixture
def kr(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setitem(sys.modules, "keyring", fake)
    return fake


@pytest.fixture
def no_keyring(monkeypatch):
    monkeypatch.setitem(sys.modules, "keyring", None)       # `import keyring` raises ImportError


# ── resolution order ────────────────────────────────────────────────────────
def test_file_is_the_fallback_so_nothing_changes_for_existing_installs(no_keyring):
    assert cred.get_secret("gemini_api_key", {"gemini_api_key": f"  {KEY}  "}) == KEY
    assert cred.source_of("gemini_api_key", {"gemini_api_key": KEY}) == "file"
    assert cred.get_secret("gemini_api_key", {}) == ""
    assert cred.source_of("gemini_api_key", {}) is None


def test_keyring_beats_the_file(kr):
    kr.store[("aether", "gemini_api_key")] = "from-keyring"
    assert cred.get_secret("gemini_api_key", {"gemini_api_key": "from-file"}) == "from-keyring"
    assert cred.source_of("gemini_api_key", {"gemini_api_key": "from-file"}) == "keyring"


def test_environment_beats_keyring_and_file(kr, monkeypatch):
    kr.store[("aether", "gemini_api_key")] = "from-keyring"
    monkeypatch.setenv("GEMINI_API_KEY", "from-env-plain")
    assert cred.get_secret("gemini_api_key", {"gemini_api_key": "f"}) == "from-env-plain"
    monkeypatch.setenv("AETHER_GEMINI_API_KEY", "from-env-prefixed")       # the prefixed name wins
    assert cred.get_secret("gemini_api_key", {}) == "from-env-prefixed"
    assert cred.source_of("gemini_api_key", {}) == "env"


def test_blank_or_whitespace_values_never_win(kr, monkeypatch):
    monkeypatch.setenv("AETHER_GEMINI_API_KEY", "   ")
    kr.store[("aether", "gemini_api_key")] = "  "
    assert cred.get_secret("gemini_api_key", {"gemini_api_key": "real"}) == "real"


# ── never fatal ─────────────────────────────────────────────────────────────
def test_missing_keyring_package_falls_back_silently(no_keyring):
    assert cred.keyring_available() is False
    assert cred.get_secret("elevenlabs_api_key", {"elevenlabs_api_key": EL}) == EL


def test_a_broken_keyring_backend_falls_back_and_warns_once_without_the_value(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "keyring", FakeKeyring(fail="get"))
    with caplog.at_level(logging.WARNING, logger="aether"):
        assert cred.get_secret("gemini_api_key", {"gemini_api_key": KEY}) == KEY
        cred.clear_cache()
        assert cred.get_secret("gemini_api_key", {"gemini_api_key": KEY}) == KEY
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 1 and "keyring unavailable" in msgs[0]
    assert KEY not in " ".join(msgs)


def test_the_value_is_never_logged(kr, caplog):
    kr.store[("aether", "gemini_api_key")] = KEY
    with caplog.at_level(logging.DEBUG, logger="aether"):
        cred.get_secret("gemini_api_key", {})
        cred.source_of("gemini_api_key", {})
    assert KEY not in caplog.text


# ── cache ───────────────────────────────────────────────────────────────────
def test_keyring_is_not_asked_on_every_request_but_refreshes(kr, monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(cred.time, "monotonic", lambda: t[0])
    kr.store[("aether", "gemini_api_key")] = "v1"
    for _ in range(20):
        assert cred.get_secret("gemini_api_key", {}) == "v1"
    assert kr.gets == 1                                         # cached
    kr.store[("aether", "gemini_api_key")] = "v2"               # changed by another process
    assert cred.get_secret("gemini_api_key", {}) == "v1"
    t[0] += cred.CACHE_SECONDS + 1
    assert cred.get_secret("gemini_api_key", {}) == "v2"        # picked up after the TTL


def test_set_and_delete_clear_the_cache(kr):
    assert cred.get_secret("gemini_api_key", {}) == ""
    cred.set_secret("gemini_api_key", KEY)
    assert cred.get_secret("gemini_api_key", {}) == KEY          # visible at once
    assert cred.delete_secret("gemini_api_key") is True
    assert cred.get_secret("gemini_api_key", {}) == ""
    assert cred.delete_secret("gemini_api_key") is False


def test_set_secret_errors_are_clean(no_keyring, monkeypatch):
    with pytest.raises(RuntimeError, match="not installed"):
        cred.set_secret("gemini_api_key", KEY)
    monkeypatch.setitem(sys.modules, "keyring", FakeKeyring(fail="set"))
    with pytest.raises(RuntimeError, match="refused") as ei:
        cred.set_secret("gemini_api_key", KEY)
    assert KEY not in str(ei.value)
    monkeypatch.setitem(sys.modules, "keyring", FakeKeyring())
    with pytest.raises(RuntimeError, match="empty"):
        cred.set_secret("gemini_api_key", "   ")


def test_works_with_the_real_keyring_library_and_an_in_memory_backend():
    keyring = pytest.importorskip("keyring")
    from keyring.backend import KeyringBackend

    class Mem(KeyringBackend):
        priority = 1
        def __init__(self): self.d = {}
        def get_password(self, s, u): return self.d.get((s, u))
        def set_password(self, s, u, p): self.d[(s, u)] = p
        def delete_password(self, s, u):
            from keyring.errors import PasswordDeleteError
            if (s, u) not in self.d:
                raise PasswordDeleteError("missing")
            del self.d[(s, u)]
    old = keyring.get_keyring()
    keyring.set_keyring(Mem())
    try:
        sys.modules.pop("keyring", None); sys.modules["keyring"] = keyring
        cred.set_secret("elevenlabs_api_key", EL)
        assert cred.get_secret("elevenlabs_api_key", {}) == EL and cred.source_of("elevenlabs_api_key", {}) == "keyring"
        assert cred.delete_secret("elevenlabs_api_key") is True
        assert cred.delete_secret("elevenlabs_api_key") is False      # real PasswordDeleteError handled
    finally:
        keyring.set_keyring(old)


# ── the three consumers ─────────────────────────────────────────────────────
def test_llm_client_uses_the_keyring_key_for_gemini_auth_and_escalation(kr, monkeypatch):
    import core.llm_client as lc
    kr.store[("aether", "gemini_api_key")] = "kr-key"
    cfg = {"gemini_api_key": "", "gemini_escalation_model": "gem-x"}
    monkeypatch.setattr(lc, "_load_config", lambda: dict(cfg))
    assert lc._auth_headers("gemini", dict(cfg)) == {"Authorization": "Bearer kr-key"}
    assert lc.get_gemini_settings() == ("kr-key", "gem-x")
    assert lc._auth_headers("ollama", dict(cfg)) == {}                  # other providers unchanged


def test_llm_client_env_key_and_file_fallback(kr, monkeypatch):
    import core.llm_client as lc
    monkeypatch.setattr(lc, "_load_config", lambda: {"gemini_api_key": "file-key"})
    assert lc.get_gemini_settings()[0] == "file-key"                    # unchanged behaviour
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    assert lc.get_gemini_settings()[0] == "env-key"
    assert lc._auth_headers("gemini", {"gemini_api_key": "file-key"}) == {"Authorization": "Bearer env-key"}


def test_no_key_anywhere_still_means_no_auth_header_and_no_escalation(no_keyring, monkeypatch):
    import core.llm_client as lc
    monkeypatch.setattr(lc, "_load_config", lambda: {})
    assert lc._auth_headers("gemini", {}) == {} and lc.get_gemini_settings()[0] == ""


def test_tts_elevenlabs_key_comes_from_the_keyring(kr, monkeypatch):
    # core.tts imports sounddevice at import time, which needs PortAudio: stub it so this
    # test runs on machines without audio libraries too
    monkeypatch.setitem(sys.modules, "sounddevice", sys.modules.get("sounddevice") or types.ModuleType("sounddevice"))
    sys.modules.pop("core.tts", None)
    import core.tts as tts
    kr.store[("aether", "elevenlabs_api_key")] = "el-from-kr"
    seen = {}

    class FakeEngine:
        def __init__(self, api_key, voice_id): seen["key"] = api_key
    monkeypatch.setattr(tts, "ElevenLabsTTSEngine", FakeEngine)
    monkeypatch.setattr(tts, "TTSPlayer", lambda engine: engine)
    tts.create_tts_player({"tts_engine": "elevenlabs", "elevenlabs_api_key": ""})
    assert seen["key"] == "el-from-kr"


# ── verify report ───────────────────────────────────────────────────────────
def test_verify_report_shows_where_a_key_comes_from_never_the_key(kr, monkeypatch):
    import tools.verify_local as vl
    kr.store[("aether", "gemini_api_key")] = KEY
    monkeypatch.setattr(vl, "_resource_snapshot", lambda: None)
    monkeypatch.setattr(vl.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    vl.out.clear()
    vl.check_env()
    text = "\n".join(vl.out)
    assert "gemini_api_key" in text and "(keyring)" in text and KEY not in text


# ── the CLI ─────────────────────────────────────────────────────────────────
@pytest.fixture
def cfgfile(tmp_path):
    p = tmp_path / "api_keys.json"
    p.write_text(json.dumps({"llm_provider": "ollama", "llm_model": "qwen2.5:7b", "tts_speed": 1.1,
                             "gemini_api_key": KEY, "elevenlabs_api_key": ""}, indent=4))
    return p


def run(*argv):
    return secrets_cli.main(list(argv))


def test_cli_status_never_prints_a_key(kr, cfgfile, capsys):
    assert run("--config", str(cfgfile), "status") == 0
    out = capsys.readouterr().out
    assert "gemini_api_key" in out and "via file" in out and KEY not in out
    assert "elevenlabs_api_key" in out and "not set" in out


def test_cli_migrate_is_a_dry_run_by_default(kr, cfgfile, capsys):
    before = cfgfile.read_text()
    assert run("--config", str(cfgfile), "migrate") == 0
    assert cfgfile.read_text() == before and kr.store == {}
    assert "DRY RUN" in capsys.readouterr().out


def test_cli_migrate_apply_moves_verifies_blanks_and_preserves_other_settings(kr, cfgfile, capsys):
    assert run("--config", str(cfgfile), "migrate", "--apply") == 0
    cfg = json.loads(cfgfile.read_text())
    assert cfg["gemini_api_key"] == "" and cfg["llm_model"] == "qwen2.5:7b" and cfg["tts_speed"] == 1.1
    assert kr.store[("aether", "gemini_api_key")] == KEY
    assert KEY not in cfgfile.read_text() and KEY not in capsys.readouterr().out
    assert cred.get_secret("gemini_api_key", cfg) == KEY            # the app still finds it
    assert not list(cfgfile.parent.glob("*.tmp"))                    # no temp file left behind
    assert run("--config", str(cfgfile), "migrate", "--apply") == 0  # second run: nothing to do


def test_cli_migrate_keeps_the_key_in_the_file_if_readback_does_not_match(monkeypatch, cfgfile, capsys):
    monkeypatch.setitem(sys.modules, "keyring", FakeKeyring(corrupt=True))
    assert run("--config", str(cfgfile), "migrate", "--apply") == 1          # non-zero: it failed
    assert json.loads(cfgfile.read_text())["gemini_api_key"] == KEY    # NOT lost
    assert "NOT moved" in capsys.readouterr().out


def test_cli_migrate_keeps_the_key_if_the_keyring_refuses_the_write(monkeypatch, cfgfile):
    monkeypatch.setitem(sys.modules, "keyring", FakeKeyring(fail="set"))
    assert run("--config", str(cfgfile), "migrate", "--apply") == 1
    assert json.loads(cfgfile.read_text())["gemini_api_key"] == KEY


def test_cli_migrate_without_the_keyring_package_changes_nothing(no_keyring, cfgfile, capsys):
    before = cfgfile.read_text()
    assert run("--config", str(cfgfile), "migrate", "--apply") == 1
    assert cfgfile.read_text() == before and "pip install keyring" in capsys.readouterr().out


def test_cli_migrate_with_an_unreadable_config_changes_nothing(kr, tmp_path, capsys):
    bad = tmp_path / "api_keys.json"
    bad.write_text("{ not json")
    assert run("--config", str(bad), "migrate", "--apply") == 1
    assert bad.read_text() == "{ not json"


def test_cli_set_and_delete(kr, cfgfile, monkeypatch, capsys):
    monkeypatch.setattr(secrets_cli.getpass, "getpass", lambda prompt="": f"  {EL}  ")
    assert run("--config", str(cfgfile), "set", "elevenlabs_api_key") == 0
    assert kr.store[("aether", "elevenlabs_api_key")] == EL
    assert EL not in capsys.readouterr().out
    assert run("--config", str(cfgfile), "delete", "elevenlabs_api_key") == 0
    assert ("aether", "elevenlabs_api_key") not in kr.store
    assert run("--config", str(cfgfile), "set", "some_random_name") == 2
    assert run("--config", str(cfgfile), "delete", "some_random_name") == 2


def test_cli_set_with_no_keyring_reports_cleanly(no_keyring, cfgfile, monkeypatch, capsys):
    monkeypatch.setattr(secrets_cli.getpass, "getpass", lambda prompt="": KEY)
    assert run("--config", str(cfgfile), "set", "gemini_api_key") == 1
    out = capsys.readouterr().out
    assert "Not stored" in out and KEY not in out


def test_cli_migrate_partial_failure_moves_the_good_key_keeps_the_bad_one_and_exits_nonzero(monkeypatch, cfgfile):
    class FailsOnlyEleven(FakeKeyring):
        def set_password(self, s, n, v):
            if n == "elevenlabs_api_key":
                raise RuntimeError("locked")
            super().set_password(s, n, v)
    fake = FailsOnlyEleven()
    monkeypatch.setitem(sys.modules, "keyring", fake)
    cfg = json.loads(cfgfile.read_text()); cfg["elevenlabs_api_key"] = EL
    cfgfile.write_text(json.dumps(cfg))
    assert run("--config", str(cfgfile), "migrate", "--apply") == 1
    out = json.loads(cfgfile.read_text())
    assert out["gemini_api_key"] == "" and fake.store[("aether", "gemini_api_key")] == KEY
    assert out["elevenlabs_api_key"] == EL                                 # the failed one is NOT lost
