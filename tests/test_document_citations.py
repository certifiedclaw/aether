from types import SimpleNamespace

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from actions import pdf_tools
from integrations import llama_index_rag
from memory.rag import RAGEngine


class FakeDocument:
    def __init__(self, pages):
        self.pages = pages
        self.closed = False

    def __iter__(self):
        return iter(self.pages)

    def __len__(self):
        return len(self.pages)

    def close(self):
        self.closed = True


class FakePage:
    def __init__(self, text):
        self.text = text

    def get_text(self):
        return self.text


def test_pdf_text_extraction_includes_page_markers(monkeypatch):
    doc = FakeDocument([FakePage("First page"), FakePage("Second page")])
    monkeypatch.setattr(pdf_tools, "_open", lambda path: doc)

    text = pdf_tools.extract_text("notes.pdf")

    assert text == "[Page 1]\nFirst page\n\n[Page 2]\nSecond page"
    assert doc.closed


def test_pdf_search_reports_page_and_handles_blank_or_missing_queries(monkeypatch):
    doc = FakeDocument([
        FakePage("First page mentions Aether."),
        FakePage("Second page mentions Aether again."),
    ])
    monkeypatch.setattr(pdf_tools, "_open", lambda path: doc)

    result = pdf_tools.search_in_document("notes.pdf", "Aether")
    assert "[1] notes.pdf, page 1:" in result
    assert "[2] notes.pdf, page 2:" in result
    assert pdf_tools.search_in_document("notes.pdf", "  ") == "Please provide a search query."
    assert "'missing' not found in notes.pdf" == pdf_tools.search_in_document("notes.pdf", "missing")


def test_rag_pdf_reader_preserves_page_boundaries(monkeypatch):
    doc = FakeDocument([FakePage("Page one text"), FakePage("Page two text")])
    fitz = ModuleType("fitz")
    fitz.open = lambda path: doc
    monkeypatch.setitem(sys.modules, "fitz", fitz)
    engine = object.__new__(RAGEngine)

    text = engine._read(Path("report.pdf"))

    assert text == "[Page 1]\nPage one text\n\n[Page 2]\nPage two text"
    assert doc.closed


def test_basic_rag_chunks_and_answers_carry_source_page_citations(monkeypatch):
    engine = object.__new__(RAGEngine)
    engine._chunk_size = 80
    engine._overlap = 0
    engine._top_k = 5
    engine._chunks = [{"source": "notes.md", "text": "Indexed passage", "page": None}]

    text = "[Page 1]\n" + ("first page content " * 6) + "[Page 2]\n" + ("second page content " * 6)
    chunks = engine._chunk(text, Path("report.pdf"))
    assert {chunk["page"] for chunk in chunks} == {1, 2}
    assert all("[Page " not in chunk["text"] for chunk in chunks)

    engine._hybrid_retrieve = lambda question: [
        {"source": "report.pdf", "text": "Evidence from page two.", "offset": 80, "page": 2}
    ]
    import core.llm_client
    prompts = []
    monkeypatch.setattr(
        core.llm_client, "call_llm_text",
        lambda prompt: prompts.append(prompt) or "The report supports this [1].",
    )

    answer = engine.query("What does the report say?", router=object())

    assert "matching source marker such as [1]" in prompts[0]
    assert "[1] report.pdf, page 2" in prompts[0]
    assert "The report supports this [1]." in answer
    assert "Sources:\n[1] report.pdf, page 2" in answer


def test_llama_index_query_uses_inline_citation_engine_and_lists_pages(monkeypatch):
    source_nodes = [
        SimpleNamespace(node=SimpleNamespace(metadata={"file_name": "notes.md", "page_label": "3"})),
        SimpleNamespace(node=SimpleNamespace(metadata={"file_name": "notes.md", "page_label": "3"})),
    ]

    class QueryResult:
        def __init__(self):
            self.source_nodes = source_nodes

        def __str__(self):
            return "Supported answer [1] [2]."

    result = QueryResult()

    class CitationQueryEngine:
        @classmethod
        def from_args(cls, index, similarity_top_k):
            assert index is sentinel_index
            assert similarity_top_k == 5
            return SimpleNamespace(query=lambda question: result)

    sentinel_index = object()
    monkeypatch.setitem(llama_index_rag._index_cache, "test", sentinel_index)
    llama_index = ModuleType("llama_index")
    core = ModuleType("llama_index.core")
    query_engine = ModuleType("llama_index.core.query_engine")
    query_engine.CitationQueryEngine = CitationQueryEngine
    core.query_engine = query_engine
    llama_index.core = core
    monkeypatch.setitem(sys.modules, "llama_index", llama_index)
    monkeypatch.setitem(sys.modules, "llama_index.core", core)
    monkeypatch.setitem(sys.modules, "llama_index.core.query_engine", query_engine)

    answer = llama_index_rag.li_query("question", collection="test", mode="vector")

    assert answer.count("Supported answer [1] [2].") == 1
    assert answer.endswith("Sources:\n[1] notes.md, page 3\n[2] notes.md, page 3")
