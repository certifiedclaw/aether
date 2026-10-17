from actions import web_search as ws


RESULTS = [
    {
        "title": "Official release notes",
        "snippet": "Python 3.14 release information.",
        "url": "https://docs.example.org/python-314",
    },
    {
        "title": "Project announcement",
        "snippet": "The release was announced in October.",
        "url": "https://example.net/releases/python-314",
    },
]


def test_search_returns_numbered_source_links(monkeypatch):
    captured = {}
    monkeypatch.setattr(ws, "_ddg_search", lambda query: RESULTS)

    def summarize(query, raw_results):
        captured["query"] = query
        captured["raw_results"] = raw_results
        return "Python 3.14 is described in the release notes [1]."

    monkeypatch.setattr(ws, "_llm_summarize", summarize)

    answer = ws.web_search({"query": "Python 3.14 release"})

    assert captured["query"] == "Python 3.14 release"
    assert "[1] Official release notes" in captured["raw_results"]
    assert "[2] Project announcement" in captured["raw_results"]
    assert "Sources:\n[1] Official release notes — https://docs.example.org/python-314" in answer
    assert "[2] Project announcement — https://example.net/releases/python-314" in answer


def test_compare_returns_source_links_and_uses_them_in_summary(monkeypatch):
    searched = []
    captured = {}

    def search(query, max_results):
        searched.append((query, max_results))
        return RESULTS

    def summarize(query, raw_results):
        captured["query"] = query
        captured["raw_results"] = raw_results
        return "The comparison is based on the available reports [1] and [2]."

    monkeypatch.setattr(ws, "_ddg_search", search)
    monkeypatch.setattr(ws, "_llm_summarize", summarize)

    answer = ws.web_search({
        "mode": "compare",
        "items": ["Aether", "Ollama"],
        "aspect": "features",
    })

    assert searched == [("Aether features", 3), ("Ollama features", 3)]
    assert "[1] Official release notes" in captured["raw_results"]
    assert "[3] Official release notes" in captured["raw_results"]
    assert "[4] Project announcement" in captured["raw_results"]
    assert "[4] Project announcement — https://example.net/releases/python-314" in answer


def test_search_without_valid_links_does_not_summarize(monkeypatch):
    monkeypatch.setattr(ws, "_ddg_search", lambda query: [
        {"title": "Unsafe", "snippet": "No web URL.", "url": "javascript:alert(1)"},
        {"title": "Missing URL", "snippet": "Cannot cite this result."},
    ])
    monkeypatch.setattr(
        ws, "_llm_summarize",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not summarize")),
    )

    answer = ws.web_search({"query": "anything"})

    assert answer == "No web results found for: anything"


def test_langchain_search_returns_numbered_sources(monkeypatch):
    from integrations import langchain_tools

    monkeypatch.setattr(
        ws, "_ddg_search",
        lambda query, max_results: RESULTS[:max_results],
    )

    answer = langchain_tools.lc_web_search("Python release", num_results=1)

    assert "[1] Official release notes" in answer
    assert "Sources:\n[1] Official release notes — https://docs.example.org/python-314" in answer
    assert "https://example.net/releases/python-314" not in answer


def test_research_agent_sources_are_deduplicated_and_attached():
    from types import SimpleNamespace
    from integrations.langchain_tools import _research_source_rows

    search = SimpleNamespace(tool="web_search")
    wikipedia = SimpleNamespace(tool="wikipedia")
    steps = [
        (search, "Sources:\n[1] Release notes — https://docs.example.org/python-314"),
        (wikipedia, "Sources:\n[1] Wikipedia — https://wikipedia.org/python"),
        (search, "Sources:\n[1] Release notes — https://docs.example.org/python-314\n"
                 "[2] Project announcement — https://example.net/releases/python-314"),
    ]

    rows = _research_source_rows(steps)

    assert rows == [
        {"title": "Release notes", "url": "https://docs.example.org/python-314"},
        {"title": "Project announcement", "url": "https://example.net/releases/python-314"},
    ]


def test_llm_summary_requests_only_supplied_citations(monkeypatch):
    from core import llm_client

    captured = {}

    def call_llm_text(prompt, system):
        captured["prompt"] = prompt
        captured["system"] = system
        return "Answer [1]."

    monkeypatch.setattr(llm_client, "call_llm_text", call_llm_text)

    assert ws._llm_summarize("question", "[1] Source — https://example.org") == "Answer [1]."
    assert "Never invent sources" in captured["system"]
    assert "cite the relevant sources" in captured["prompt"]


def test_tool_description_promises_numbered_sources():
    from core.tool_declarations import TOOL_DECLARATIONS

    declaration = next(item for item in TOOL_DECLARATIONS if item["name"] == "web_search")
    assert "numbered source links" in declaration["description"]
