"""Roadmap #10: failures in actions/ must not look like empty/negative results."""
from actions import web_search


def test_compare_marks_failed_item_instead_of_leaving_it_blank(monkeypatch):
    def fake_search(query, max_results=6):
        if "bad" in query:
            raise RuntimeError("network down")
        return [{"title": "T", "snippet": "S", "url": "https://example.com/x"}]

    seen = {}
    monkeypatch.setattr(web_search, "_ddg_search", fake_search)
    monkeypatch.setattr(web_search, "_llm_summarize",
                        lambda q, raw: seen.setdefault("raw", raw) or raw)
    out = web_search._compare(["good", "bad"], "speed")
    assert "search failed: RuntimeError" in seen["raw"]
    assert "▸ good" in seen["raw"] and "https://example.com/x" in seen["raw"]
    assert "https://example.com/x" in out
