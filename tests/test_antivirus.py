import hashlib
import json
from types import SimpleNamespace

import pytest

from actions import antivirus as antivirus_module


def test_status_reports_security_center_products_and_scanner_availability(monkeypatch):
    monkeypatch.setattr(antivirus_module.sys, "platform", "win32")
    monkeypatch.setattr(
        antivirus_module, "_windows_security_products",
        lambda: (["Microsoft Defender Antivirus", "Malwarebytes"], None),
    )
    monkeypatch.setattr(antivirus_module, "_defender_cli", lambda: "MpCmdRun.exe")
    monkeypatch.setattr(antivirus_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(antivirus_module, "_credential", lambda: "")

    report = antivirus_module.antivirus_status()

    assert "Microsoft Defender Antivirus; Malwarebytes" in report
    assert "Microsoft Defender scan command: available" in report
    assert "ClamAV scan command: not found" in report
    assert "not a health or protection check" in report


def test_auto_scan_does_not_fall_back_when_only_unsupported_product_is_registered(
    monkeypatch, tmp_path
):
    target = tmp_path / "sample.bin"
    target.write_bytes(b"sample")
    monkeypatch.setattr(antivirus_module.sys, "platform", "win32")
    monkeypatch.setattr(
        antivirus_module, "_windows_security_products",
        lambda: (["Kaspersky Endpoint Security"], None),
    )
    monkeypatch.setattr(antivirus_module, "_defender_cli", lambda: "MpCmdRun.exe")
    monkeypatch.setattr(antivirus_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        antivirus_module.subprocess, "run",
        lambda *args, **kwargs: pytest.fail("must not start an unregistered scanner"),
    )

    result = antivirus_module._scan(target, "auto")

    assert "Kaspersky Endpoint Security" in result
    assert "no compatible scan adapter" in result
    assert "No other scanner was started" in result


def test_clamav_scan_uses_argument_list_and_reports_detection(monkeypatch, tmp_path):
    target = tmp_path / "sample.bin"
    target.write_bytes(b"sample")
    scanner = tmp_path / "clamscan"
    calls = []

    monkeypatch.setattr(
        antivirus_module, "_choose_scanner",
        lambda provider: ("clamav", scanner, None),
    )

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=1, stdout="sample.bin: Eicar-Test-Signature FOUND\n", stderr="")

    monkeypatch.setattr(antivirus_module.subprocess, "run", fake_run)
    result = antivirus_module._scan(target, "clamav")

    assert calls[0][0] == [
        str(scanner), "--no-summary", "--infected", "--", str(target)
    ]
    assert calls[0][1]["timeout"] == antivirus_module._SCAN_TIMEOUT
    assert "reported a detection" in result
    assert "Eicar-Test-Signature" in result


def test_defender_scan_discloses_possible_remediation(monkeypatch, tmp_path):
    target = tmp_path / "sample.bin"
    target.write_bytes(b"sample")
    scanner = tmp_path / "MpCmdRun.exe"
    calls = []
    monkeypatch.setattr(
        antivirus_module, "_choose_scanner",
        lambda provider: ("defender", scanner, None),
    )
    monkeypatch.setattr(
        antivirus_module.subprocess, "run",
        lambda command, **kwargs: (
            calls.append((command, kwargs))
            or SimpleNamespace(returncode=0, stdout="Scan completed", stderr="")
        ),
    )

    result = antivirus_module._scan(target, "defender")

    assert calls[0][0] == [
        str(scanner), "-Scan", "-ScanType", "3", "-File", str(target)
    ]
    assert "may quarantine or remediate" in result
    assert "Protection history" in result


def test_protected_files_are_not_scanned(monkeypatch, tmp_path):
    from core import risk, safety
    from core import protected_paths

    target = tmp_path / "secret.txt"
    target.write_text("private", encoding="utf-8")
    monkeypatch.setattr(risk, "_overrides", lambda: {})
    monkeypatch.setattr(safety, "confirm_action", lambda operation, detail="": True)
    monkeypatch.setattr(protected_paths, "is_protected", lambda path: True)
    monkeypatch.setattr(
        antivirus_module, "_scan",
        lambda *args: pytest.fail("protected path must not be scanned"),
    )

    result = antivirus_module.antivirus({"action": "scan", "path": str(target)})

    assert result == protected_paths.DENIED


def test_scan_and_hash_actions_require_approval(monkeypatch, tmp_path):
    from core import risk, safety

    target = tmp_path / "sample.bin"
    target.write_bytes(b"sample")
    prompts = []
    scans = []
    monkeypatch.setattr(risk, "_overrides", lambda: {})
    monkeypatch.setattr(
        safety, "confirm_action",
        lambda operation, detail="": prompts.append((operation, detail)) or False,
    )
    monkeypatch.setattr(
        antivirus_module, "_scan",
        lambda *args: scans.append(args) or "scan ran",
    )

    result = antivirus_module.antivirus({
        "action": "scan", "provider": "clamav", "path": str(target),
    })

    assert result.startswith("Cancelled")
    assert scans == []
    assert prompts[0][0] == "antivirus.scan"
    assert "quarantine or remediate" in prompts[0][1]


def test_antivirus_schema_describes_supported_providers_and_hash_privacy():
    from core.risk import SAFE, ASK, tier_for
    from core.tool_declarations import TOOL_DECLARATIONS

    declaration = next(item for item in TOOL_DECLARATIONS if item["name"] == "antivirus")
    description = declaration["description"].lower()

    assert "microsoft defender" in description and "clamav" in description
    assert "sha-256" in description and "never uploaded" in description
    assert tier_for("antivirus", "status") == SAFE
    assert tier_for("antivirus", "scan") == ASK
    assert tier_for("antivirus", "hash_reputation") == ASK


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield self.payload

    def close(self):
        self.closed = True


def test_virustotal_sends_only_sha256_hash_and_never_file_contents(monkeypatch, tmp_path):
    target = tmp_path / "sample.bin"
    contents = b"private file contents"
    target.write_bytes(contents)
    api_key = "test-api-key"
    expected_hash = hashlib.sha256(contents).hexdigest()
    payload = json.dumps({
        "data": {"attributes": {
            "last_analysis_stats": {"malicious": 1, "harmless": 60},
            "last_analysis_date": 123,
        }},
    }).encode()
    response = _FakeResponse(payload)
    calls = []
    monkeypatch.setattr(antivirus_module, "_credential", lambda: api_key)

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return response

    monkeypatch.setattr(antivirus_module.requests, "get", fake_get)

    result = antivirus_module._hash_reputation(target)

    assert calls[0][0] == antivirus_module._VT_FILE_URL + expected_hash
    assert calls[0][1]["headers"]["x-apikey"] == api_key
    assert calls[0][1]["allow_redirects"] is False
    assert "data" not in calls[0][1] and "files" not in calls[0][1]
    assert response.closed
    assert "malicious: 1" in result
    assert "file was not uploaded" in result


def test_virustotal_missing_key_does_not_make_request(monkeypatch, tmp_path):
    target = tmp_path / "sample.bin"
    target.write_bytes(b"sample")
    monkeypatch.setattr(antivirus_module, "_credential", lambda: "")
    monkeypatch.setattr(
        antivirus_module.requests, "get",
        lambda *args, **kwargs: pytest.fail("no key means no request"),
    )

    result = antivirus_module._hash_reputation(target)

    assert "not configured" in result
    assert "No file was uploaded" in result


def test_virustotal_lookup_requires_approval(monkeypatch, tmp_path):
    from core import risk, safety

    target = tmp_path / "sample.bin"
    target.write_bytes(b"sample")
    prompts = []
    monkeypatch.setattr(risk, "_overrides", lambda: {})
    monkeypatch.setattr(
        safety, "confirm_action",
        lambda operation, detail="": prompts.append((operation, detail)) or False,
    )
    monkeypatch.setattr(
        antivirus_module, "_hash_reputation",
        lambda path: pytest.fail("hash lookup should be denied"),
    )

    result = antivirus_module.antivirus({
        "action": "hash_reputation", "path": str(target),
    })

    assert result.startswith("Cancelled")
    assert prompts[0][0] == "antivirus.hash_reputation"
    assert "Only the file's SHA-256 hash" in prompts[0][1]
