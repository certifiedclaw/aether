import json

import pytest
import requests

from actions import threat_intel


class _Response:
    def __init__(self, value=None, status=200):
        self.value = value
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def iter_content(self, chunk_size):
        assert chunk_size == 64 * 1024
        yield json.dumps(self.value).encode()


def _osv_record():
    return {
        "id": "CVE-2025-12345",
        "summary": "Example vulnerability summary.",
        "modified": "2025-01-03T00:00:00Z",
        "affected": [{"package": {"ecosystem": "PyPI", "name": "example"}}],
        "aliases": ["GHSA-abcd-efgh-ijkl"],
        "references": [
            {"type": "WEB", "url": "https://example.test/advisory"},
            {"type": "WEB", "url": "http://insecure.example.test/"},
        ],
    }


def test_threat_brief_labels_osv_and_cisa_listing(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        if url.startswith(threat_intel._OSV_URL):
            return _Response(_osv_record())
        return _Response({
            "count": 1,
            "vulnerabilities": [{
                "cveID": "CVE-2025-12345",
                "dateAdded": "2025-01-02",
                "vendorProject": "Example",
                "product": "Server",
                "requiredAction": "Apply the vendor update.",
                "dueDate": "2025-01-30",
            }]
        })

    monkeypatch.setattr(threat_intel.requests, "get", get)

    result = threat_intel.threat_intel_brief("cve-2025-12345")

    assert "Example vulnerability summary." in result
    assert "Packages listed by OSV: PyPI/example" in result
    assert "CISA KEV status: LISTED" in result
    assert "Apply the vendor update." in result
    assert "https://example.test/advisory" in result
    assert "http://insecure.example.test" not in result
    assert len(calls) == 2
    assert all(call[1]["timeout"] == threat_intel._TIMEOUT for call in calls)
    assert all(call[1]["stream"] is True for call in calls)


def test_threat_brief_distinguishes_not_listed_from_lookup_failure(monkeypatch):
    def get(url, **kwargs):
        if url.startswith(threat_intel._OSV_URL):
            return _Response(_osv_record())
        return _Response({"count": 0, "vulnerabilities": []})

    monkeypatch.setattr(threat_intel.requests, "get", get)

    result = threat_intel.threat_intel_brief("CVE-2025-12345")

    assert "CISA KEV status: NOT LISTED" in result
    assert "not proof that the vulnerability has never been exploited" in result

    def unavailable(*_args, **_kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(threat_intel.requests, "get", unavailable)
    result = threat_intel.threat_intel_brief("CVE-2025-12345")
    assert "OSV lookup unavailable" in result
    assert "CISA KEV status: unable to check" in result
    assert "not interpret a failed lookup as evidence" in result


def test_threat_brief_rejects_non_cve_input_without_network(monkeypatch):
    monkeypatch.setattr(
        threat_intel.requests,
        "get",
        lambda *_args, **_kwargs: pytest.fail("invalid IDs must not trigger requests"),
    )

    assert threat_intel.threat_intel_brief("example.com").startswith("Invalid CVE ID")
