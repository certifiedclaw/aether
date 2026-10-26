"""Tests for tools/maintenance/dep_watcher.py upstream lookups (roadmap #31).

All network access is mocked; nothing here touches the internet.
"""
from __future__ import annotations

import io
import json
import urllib.error

from tools.maintenance import dep_watcher


class _Resp:
    def __init__(self, body: bytes = b"", url: str = ""):
        self._body = body
        self._url = url

    def read(self):
        return self._body

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(b""))


def _router(routes: dict):
    """Build a fake urlopen. routes: substring-of-url -> bytes | (url_for_geturl,) | int (HTTP error)."""
    def fake(req, timeout=None):
        url = req.full_url
        for needle, result in routes.items():
            if needle in url:
                if isinstance(result, int):
                    raise _http_error(url, result)
                if isinstance(result, tuple):
                    return _Resp(url=result[0])
                return _Resp(body=result)
        raise _http_error(url, 404)
    return fake


def test_api_release_is_preferred(monkeypatch):
    monkeypatch.setattr(dep_watcher.urllib.request, "urlopen", _router({
        "api.github.com/repos/ollama/ollama/releases/latest": json.dumps({"tag_name": "v9.9.9"}).encode(),
    }))
    ver, detail = dep_watcher._github_latest_release("ollama/ollama")
    assert ver == "v9.9.9"
    assert detail == "GitHub release"


def test_rate_limited_api_falls_back_to_web_redirect(monkeypatch):
    monkeypatch.setattr(dep_watcher.urllib.request, "urlopen", _router({
        "api.github.com": 403,
        "github.com/ollama/ollama/releases/latest": ("https://github.com/ollama/ollama/releases/tag/v1.2.3",),
    }))
    ver, detail = dep_watcher._github_latest_release("ollama/ollama")
    assert ver == "v1.2.3"
    assert detail == "GitHub release (web)"


def test_pypi_fallback_when_github_unreachable(monkeypatch):
    monkeypatch.setattr(dep_watcher.urllib.request, "urlopen", _router({
        "api.github.com": 403,
        "github.com/hexgrad/kokoro/releases/latest": 403,
        "pypi.org/pypi/kokoro/json": json.dumps({"info": {"version": "0.9.4"}}).encode(),
    }))
    ver, detail = dep_watcher._github_latest_release("hexgrad/kokoro", "kokoro")
    assert ver == "0.9.4"
    assert detail == "PyPI kokoro"


def test_total_failure_reports_reasons(monkeypatch):
    monkeypatch.setattr(dep_watcher.urllib.request, "urlopen", _router({
        "api.github.com": 403,
        "github.com/ollama/ollama/releases/latest": 403,
    }))
    ver, detail = dep_watcher._github_latest_release("ollama/ollama")
    assert ver is None
    assert "rate-limited" in detail
    assert "API releases" in detail and "web" in detail and "API tags" in detail


def test_token_is_sent_when_set(monkeypatch):
    seen = {}

    def fake(req, timeout=None):
        seen["auth"] = req.get_header("Authorization")
        return _Resp(body=json.dumps({"tag_name": "v1"}).encode())

    monkeypatch.setenv("GITHUB_TOKEN", "tok123")
    monkeypatch.setattr(dep_watcher.urllib.request, "urlopen", fake)
    dep_watcher._github_latest_release("ollama/ollama")
    assert seen["auth"] == "Bearer tok123"


def test_no_token_means_no_auth_header(monkeypatch):
    seen = {}

    def fake(req, timeout=None):
        seen["auth"] = req.get_header("Authorization")
        return _Resp(body=json.dumps({"tag_name": "v1"}).encode())

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(dep_watcher.urllib.request, "urlopen", fake)
    dep_watcher._github_latest_release("ollama/ollama")
    assert seen["auth"] is None


def test_kokoro_watches_code_repo_not_hf_model_repo():
    repos = {r for r, *_ in dep_watcher.GITHUB_WATCHLIST}
    assert "hexgrad/kokoro" in repos
    assert "hexgrad/Kokoro-82M" not in repos
