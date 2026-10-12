"""Secrets files must not be readable through any tool; port scans are bounded."""
from pathlib import Path

import pytest

from core import protected_paths as pp

ROOT = Path(pp.__file__).resolve().parent.parent


def test_secret_files_are_protected():
    assert pp.is_protected(ROOT / "config" / "api_keys.json")
    assert pp.is_protected("config/../config/api_keys.json".replace("config/..", str(ROOT)))
    assert pp.is_protected(ROOT / "config" / "certs" / "server.crt")
    assert pp.is_protected(ROOT / "data" / "security" / "anything")
    assert pp.is_protected("/somewhere/else/api_keys.json")
    assert pp.is_protected("/x/id.pem") and pp.is_protected("/x/k.key")
    assert pp.is_protected(ROOT / ".env")
    assert pp.is_protected(ROOT / "config" / ".env.production")


def test_ordinary_files_are_not_protected(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hi")
    assert not pp.is_protected(f)
    assert not pp.is_protected(ROOT / "config" / "config.example.json")


def test_symlink_to_secret_is_protected(tmp_path):
    target = tmp_path / "api_keys.json"
    target.write_text("{}")
    link = tmp_path / "innocent.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert pp.is_protected(link)


def test_diff_and_word_count_refuse_secrets(tmp_path):
    from actions import extra_tools as et
    other = tmp_path / "a.txt"
    other.write_text("x")
    secret = ROOT / "config" / "api_keys.json"
    assert et.diff_files(str(secret), str(other)) == pp.DENIED
    assert et.diff_files(str(other), str(secret)) == pp.DENIED
    secret_made = not secret.exists()
    secret.parent.mkdir(exist_ok=True)
    if secret_made:
        secret.write_text('{"gemini_api_key": "LEAK"}')
    try:
        assert et.word_count(str(secret)) == pp.DENIED
    finally:
        if secret_made:
            secret.unlink()


def test_file_processor_and_pdf_refuse_secrets():
    from actions import file_processor as fp, pdf_tools
    assert fp.file_processor({"file_path": str(ROOT / "config" / "api_keys.json")}) == pp.DENIED
    assert "Access denied" in pdf_tools.extract_text(str(ROOT / "config" / "api_keys.json"))


def test_zip_refuses_protected_source_without_creating_archive(tmp_path):
    from actions import extra_tools as et
    source = tmp_path / "bundle"
    source.mkdir()
    (source / "notes.txt").write_text("ordinary", encoding="utf-8")
    (source / ".env.local").write_text("SECRET=value", encoding="utf-8")
    archive = tmp_path / "bundle.zip"
    assert et.zip_files([str(source)], str(archive)) == pp.DENIED
    assert not archive.exists()


def test_zip_refuses_protected_input_and_output_paths(tmp_path):
    from actions import extra_tools as et
    ordinary = tmp_path / "notes.txt"
    ordinary.write_text("ordinary", encoding="utf-8")
    assert et.zip_files([str(tmp_path / "api_keys.json")], str(tmp_path / "x.zip")) == pp.DENIED
    assert et.zip_files([str(ordinary)], str(tmp_path / ".env")) == pp.DENIED
    assert not (tmp_path / "x.zip").exists() and not (tmp_path / ".env").exists()


def test_unzip_refuses_secrets_and_executable_plugin_destinations(tmp_path):
    import zipfile
    from actions import extra_tools as et
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(".env.local", "SECRET=value")
    destination = tmp_path / "out"
    assert et.unzip_file(str(archive), str(destination)) == pp.DENIED
    assert not destination.exists()
    assert "plugin" in et.unzip_file(str(archive), str(ROOT / "plugins")).lower()


def test_unzip_rejects_archive_path_traversal(tmp_path):
    import zipfile
    from actions import extra_tools as et
    archive = tmp_path / "traversal.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escaped.txt", "not extracted")
    destination = tmp_path / "out"
    assert "unsafe path" in et.unzip_file(str(archive), str(destination)).lower()
    assert not (tmp_path / "escaped.txt").exists()


@pytest.mark.parametrize("member", ["../escaped.txt", r"..\\escaped.txt", ".env.local", ".env. ", "api_keys.json"])
def test_file_processor_archive_extract_rejects_unsafe_or_protected_members(tmp_path, member):
    import zipfile
    from actions import file_processor as fp
    archive = tmp_path / "malicious.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(member, "must not be written")
    destination = tmp_path / "out"

    result = fp._process_archive(archive, "extract", {"destination": str(destination)})

    if member in (".env.local", "api_keys.json"):
        assert result == pp.DENIED
    else:
        assert "unsafe path" in result.lower()
    assert not destination.exists()


@pytest.mark.parametrize("member", ["plugins/evil.py", "plugins. /evil.py"])
def test_file_processor_archive_extract_rejects_plugin_destination_and_members(tmp_path, monkeypatch, member):
    import zipfile
    from actions import file_processor as fp
    archive = tmp_path / "plugin.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(member, "raise RuntimeError('executed')")
    destination = tmp_path / "out"
    plugin_dir = destination / "plugins"
    monkeypatch.setattr(fp, "_active_plugin_dir", lambda: plugin_dir)

    result = fp._process_archive(archive, "extract", {"destination": str(destination)})

    assert "plugin" in result.lower() or "unsafe path" in result.lower()
    assert not destination.exists()
    plugin_result = fp._process_archive(
        archive, "extract", {"destination": str(plugin_dir)})
    assert "plugin" in plugin_result.lower() or "unsafe path" in plugin_result.lower()
    assert not plugin_dir.exists()


def test_file_processor_archive_extract_rejects_tar_links_and_allows_safe_tar(tmp_path, monkeypatch):
    import io
    import tarfile
    from actions import file_processor as fp
    malicious = tmp_path / "link.tar"
    with tarfile.open(malicious, "w") as archive:
        link = tarfile.TarInfo("link")
        link.type = tarfile.SYMTYPE
        link.linkname = "../outside"
        archive.addfile(link)
        payload = b"must not be written"
        member = tarfile.TarInfo("link/file.txt")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    destination = tmp_path / "tar-out"

    result = fp._process_archive(malicious, "extract", {"destination": str(destination)})

    assert "unsupported file type" in result.lower()
    assert not destination.exists()

    safe = tmp_path / "safe.tar.gz"
    with tarfile.open(safe, "w:gz") as archive:
        payload = b"safe contents"
        member = tarfile.TarInfo("folder/readme.txt")
        member.size = len(payload)
        member.mode = 0o751
        archive.addfile(member, io.BytesIO(payload))
    safe_destination = tmp_path / "safe-out"
    monkeypatch.setattr(fp, "_approve_archive_extraction", lambda *a: True)
    chmods = []
    original_chmod = Path.chmod
    def record_chmod(path, mode, *args, **kwargs):
        if path.name == "readme.txt":
            chmods.append(mode)
        return original_chmod(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "chmod", record_chmod)
    result = fp._process_archive(safe, "extract", {"destination": str(safe_destination)})
    assert "Extracted to" in result
    assert (safe_destination / "folder" / "readme.txt").read_bytes() == payload
    assert chmods == [0o751]


@pytest.mark.parametrize("ports", ["1-999999999", "0-10", "1-70000", "abc", "1-5000", "70000", ""])
def test_port_scan_rejects_unbounded_or_invalid_ranges(ports):
    from actions import osint_tools as ot
    out = ot.port_scan("127.0.0.1", ports)
    assert out.startswith(("Invalid", "Too many", "No ports")), out


def test_port_scan_small_range_still_runs():
    from actions import osint_tools as ot
    out = ot.port_scan("127.0.0.1", "1-3")
    assert not out.startswith(("Invalid", "Too many", "No ports"))
