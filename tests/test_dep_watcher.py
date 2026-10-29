"""Tests for tools/maintenance/dep_watcher.py upstream lookups (roadmap #31).

All network access is mocked; nothing here touches the internet.
"""
import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from tools.maintenance import dep_watcher as dw


class _Resp:
    """Minimal context-manager stand-in for an urlopen() response."""

    def __init__(self, body=b"", url=""):
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


def _json(obj):
    return _Resp(json.dumps(obj).encode("utf-8"))


def _http_error(url, code):
    return urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(b""))


def _router(routes):
    """Build an urlopen replacement from {url_substring: response_or_exception}.
    Unmatched URLs raise a 404 so a missing route can't silently succeed."""

    def _urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        for needle, result in routes.items():
            if needle in url:
                if isinstance(result, Exception):
                    raise result
                return result
        raise _http_error(url, 404)

    return _urlopen


def _patch(routes):
    return patch.object(dw.urllib.request, "urlopen", _router(routes))


def test_api_release_is_preferred():
    routes = {"api.github.com/repos/ollama/ollama/releases/latest": _json({"tag_name": "v0.99.0"})}
    with _patch(routes):
        assert dw._github_latest_release("ollama/ollama") == ("v0.99.0", None)


def test_falls_back_to_tags_when_no_release():
    routes = {
        "releases/latest": _http_error("x", 404),
        "api.github.com/repos/a/b/tags": _json([{"name": "v1.2.3"}, {"name": "v1.2.2"}]),
    }
    with _patch(routes):
        assert dw._github_latest_release("a/b") == ("v1.2.3", None)


def test_rate_limited_api_falls_back_to_releases_redirect():
    """The real-world failure: anonymous API rate limit -> 403 on every API call."""
    routes = {
        "api.github.com": _http_error("x", 403),
        "https://github.com/ollama/ollama/releases/latest": _Resp(
            url="https://github.com/ollama/ollama/releases/tag/v0.12.5"
        ),
    }
    with _patch(routes):
        assert dw._github_latest_release("ollama/ollama") == ("v0.12.5", None)


def test_redirect_tag_is_url_decoded():
    routes = {
        "api.github.com": _http_error("x", 403),
        "https://github.com/o/r/releases/latest": _Resp(url="https://github.com/o/r/releases/tag/rel%2F1.0"),
    }
    with _patch(routes):
        assert dw._github_latest_release("o/r") == ("rel/1.0", None)


def test_repo_without_releases_uses_pypi_fallback():
    routes = {
        "api.github.com": _http_error("x", 404),
        # github.com redirects to the bare /releases page when nothing is published
        "https://github.com/hexgrad/kokoro/releases/latest": _Resp(url="https://github.com/hexgrad/kokoro/releases"),
        "pypi.org/pypi/kokoro/json": _json({"info": {"version": "0.9.4"}}),
    }
    with _patch(routes):
        version, err = dw._github_latest_release("hexgrad/kokoro", "kokoro")
    assert version == "0.9.4 (PyPI)"
    assert err is None


def test_total_failure_reports_every_reason_not_a_blank_unknown():
    routes = {
        "api.github.com": _http_error("x", 403),
        "https://github.com/": _http_error("x", 503),
        "pypi.org": urllib.error.URLError("dns down"),
    }
    with _patch(routes):
        version, err = dw._github_latest_release("o/r", "r")
    assert version is None
    assert "API releases HTTP 403" in err
    assert "API tags HTTP 403" in err
    assert "releases page HTTP 503" in err
    assert "PyPI network error: dns down" in err


def test_no_pypi_fallback_means_no_pypi_request():
    seen = []

    def _urlopen(req, timeout=None):
        seen.append(req.full_url)
        raise _http_error(req.full_url, 403)

    with patch.object(dw.urllib.request, "urlopen", _urlopen):
        version, _ = dw._github_latest_release("ollama/ollama", None)
    assert version is None
    assert not any("pypi.org" in u for u in seen)


def test_timeout_and_bad_json_are_described():
    def _timeout(req, timeout=None):
        raise TimeoutError()

    with patch.object(dw.urllib.request, "urlopen", _timeout):
        assert dw._fetch_json("https://example.invalid/x") == (None, "timeout")

    with _patch({"example.invalid": _Resp(b"<html>not json")}):
        assert dw._fetch_json("https://example.invalid/x") == (None, "unparseable response")


def test_github_token_only_sent_to_github_api(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    assert dw._headers("https://api.github.com/repos/a/b/tags")["Authorization"] == "Bearer secret-token"
    assert "Authorization" not in dw._headers("https://pypi.org/pypi/x/json")
    assert "Authorization" not in dw._headers("https://github.com/a/b/releases/latest")


def test_no_token_means_no_auth_header(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert "Authorization" not in dw._headers("https://api.github.com/repos/a/b/tags")


def test_watchlist_kokoro_points_at_github_library_not_hf_model_repo():
    repos = {repo: pypi for repo, _label, _why, pypi in dw.GITHUB_WATCHLIST}
    assert "hexgrad/Kokoro-82M" not in repos  # Hugging Face model repo, no GitHub releases
    assert repos["hexgrad/kokoro"] == "kokoro"
    assert repos["SYSTRAN/faster-whisper"] == "faster-whisper"
    assert repos["ollama/ollama"] is None  # PyPI "ollama" is the client, not the server


def test_run_check_surfaces_reason_in_report(monkeypatch):
    monkeypatch.setattr(dw, "_parse_requirements", lambda: ["somepkg"])

    def _urlopen(req, timeout=None):
        raise _http_error(req.full_url, 403)

    with patch.object(dw.urllib.request, "urlopen", _urlopen):
        data = dw.run_check()
    assert data["pip"][0]["latest"] == "unknown (lookup failed: PyPI HTTP 403)"
    ollama = next(i for i in data["infra"] if i["repo"] == "ollama/ollama")
    assert ollama["latest"].startswith("unknown (lookup failed: API releases HTTP 403")
    report = dw.render_report(data)
    assert "https://github.com/hexgrad/kokoro" in report
