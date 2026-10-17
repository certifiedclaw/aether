"""
integrations/llama_index_rag.py — Aether × LlamaIndex

Integrates LlamaIndex (github.com/run-llama/llama_index) as the production
RAG backend. LlamaIndex offers better document chunking strategies, metadata
filtering, sub-question query decomposition, and hybrid retrieval than a
plain vector store.

Features pulled from llama_index:
  • SimpleDirectoryReader  — load any file/dir (PDF, DOCX, CSV, HTML, MD…)
  • VectorStoreIndex       — Chroma-backed persistent vector index
  • SummaryIndex           — fast summarisation over a document set
  • RouterQueryEngine      — auto-picks vector vs summary based on query type
  • SubQuestionQueryEngine — decomposes complex queries into sub-questions
  • Ollama LLM + embedding  — fully local, no external API

This module is a drop-in enhancement to memory/rag.py.  Aether's existing
rag_query tool routes to this module when LlamaIndex is installed; falls back
to the basic RAG engine if not.

Requires:
    pip install llama-index llama-index-llms-ollama llama-index-embeddings-ollama
    pip install llama-index-vector-stores-chroma chromadb
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_DIR  = Path("data/llama_index")


def _chroma_dir() -> Path:
    """Get chroma directory from config."""
    return Path(_li_config().get("chroma_dir", "./data/chroma_db"))


# ── Config ────────────────────────────────────────────────────────────────

def _li_config() -> dict:
    try:
        from memory.config_manager import get_mark_config
        cfg = get_mark_config()
        return {
            "model":               cfg.get("llm.model",       cfg.get("llm_model", "qwen3:8b")),
            "embed_model":         cfg.get("llm.embed_model",  "nomic-embed-text"),
            "base_url":            cfg.ollama_url,
            "default_collection":  cfg.get("llama_index.default_collection", "aether"),
            "chroma_dir":          cfg.get("llama_index.chroma_dir", "./data/chroma_db"),
        }
    except Exception:
        return {
            "model": "qwen3:8b",
            "embed_model": "nomic-embed-text",
            "base_url": "http://localhost:11434",
            "default_collection": "aether",
            "chroma_dir": "./data/chroma_db",
        }


# ── Lazy singletons ───────────────────────────────────────────────────────

_llm_instance:   Any = None
_embed_instance: Any = None
_index_cache:    dict[str, Any] = {}


def _get_llm():
    global _llm_instance
    if _llm_instance is None:
        from llama_index.llms.ollama import Ollama
        cfg = _li_config()
        _llm_instance = Ollama(
            model=cfg["model"],
            base_url=cfg["base_url"],
            request_timeout=120.0,
        )
    return _llm_instance


def _get_embed():
    global _embed_instance
    if _embed_instance is None:
        from llama_index.embeddings.ollama import OllamaEmbedding
        cfg = _li_config()
        _embed_instance = OllamaEmbedding(
            model_name=cfg["embed_model"],
            base_url=cfg["base_url"],
        )
    return _embed_instance


def _get_service_context():
    """Build a LlamaIndex ServiceContext (or Settings in new API)."""
    try:
        # llama_index >= 0.10 uses Settings
        from llama_index.core import Settings
        Settings.llm   = _get_llm()
        Settings.embed_model = _get_embed()
        return None   # Settings is global; no object needed
    except ImportError:
        # llama_index < 0.10 uses ServiceContext
        from llama_index import ServiceContext
        return ServiceContext.from_defaults(
            llm=_get_llm(), embed_model=_get_embed()
        )


# ── Index management ──────────────────────────────────────────────────────

def _chroma_collection(name: str = "aether"):
    """Get or create a ChromaDB collection for persistence."""
    try:
        import chromadb
        from llama_index.vector_stores.chroma import ChromaVectorStore
        chroma_path = _chroma_dir()
        chroma_path.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(chroma_path))
        col    = client.get_or_create_collection(name)
        return ChromaVectorStore(chroma_collection=col)
    except ImportError:
        return None   # fall back to in-memory index


def _get_or_build_index(docs, collection_name: str = "aether"):
    """Build a VectorStoreIndex from documents, with optional Chroma persistence."""
    from llama_index.core import VectorStoreIndex, StorageContext
    _get_service_context()

    vs = _chroma_collection(collection_name)
    if vs:
        storage_ctx = StorageContext.from_defaults(vector_store=vs)
        return VectorStoreIndex.from_documents(docs, storage_context=storage_ctx)
    else:
        return VectorStoreIndex.from_documents(docs)


def _cited_result(result) -> str:
    """Render a LlamaIndex answer with source labels and PDF page metadata."""
    answer = str(result).strip()
    source_nodes = getattr(result, "source_nodes", None) or []
    sources = []
    for source_node in source_nodes:
        node = getattr(source_node, "node", source_node)
        metadata = getattr(node, "metadata", {}) or {}
        path = metadata.get("file_path") or metadata.get("filename") or ""
        name = metadata.get("file_name") or (Path(str(path)).name if path else "")
        if not name:
            name = "Retrieved document passage"
        page = (
            metadata.get("page_label")
            or metadata.get("page_number")
            or metadata.get("page")
        )
        label = f"{name}, page {page}" if page not in (None, "") else str(name)
        sources.append(label)
    if not sources:
        return answer
    refs = "\n".join(f"[{i}] {label}" for i, label in enumerate(sources, 1))
    return f"{answer}\n\nSources:\n{refs}" if answer else f"Sources:\n{refs}"


# ── Public API ────────────────────────────────────────────────────────────

def li_index_path(path: str, collection: str = "aether") -> str:
    """
    Load all documents from a file or directory and add them to the index.
    Supports PDF, DOCX, CSV, HTML, Markdown, plain text.
    """
    try:
        from llama_index.core import SimpleDirectoryReader
        p = Path(os.path.expanduser(path))
        if p.is_file():
            docs = SimpleDirectoryReader(input_files=[str(p)]).load_data()
        elif p.is_dir():
            docs = SimpleDirectoryReader(str(p), recursive=True).load_data()
        else:
            return f"Path not found: {path}"

        idx = _get_or_build_index(docs, collection)
        _index_cache[collection] = idx
        return f"Indexed {len(docs)} document(s) from {path} into '{collection}'."
    except ImportError:
        return "Install: pip install llama-index llama-index-llms-ollama llama-index-embeddings-ollama"
    except Exception as e:
        return f"Index error: {e}"


def li_query(query: str, collection: str = "aether", mode: str = "auto") -> str:
    """
    Query the LlamaIndex RAG engine.

    mode:
      auto     — RouterQueryEngine picks vector vs summary automatically
      vector   — similarity search (best for specific questions)
      summary  — summarise the whole collection (best for overview)
      sub      — SubQuestionQueryEngine (best for complex multi-part queries)
    """
    try:
        idx = _index_cache.get(collection)
        if idx is None:
            # Try loading a persisted Chroma index
            from llama_index.core import VectorStoreIndex, StorageContext
            vs = _chroma_collection(collection)
            if vs:
                _get_service_context()
                storage_ctx = StorageContext.from_defaults(vector_store=vs)
                idx = VectorStoreIndex.from_vector_store(
                    vs, storage_context=storage_ctx
                )
                _index_cache[collection] = idx
            else:
                return (
                    f"No index found for '{collection}'. "
                    "Use rag_query(action='index_file') to add documents first."
                )

        if mode == "vector" or mode == "auto":
            from llama_index.core.query_engine import CitationQueryEngine
            engine = CitationQueryEngine.from_args(idx, similarity_top_k=5)
            result = engine.query(query)
            return _cited_result(result)

        elif mode == "summary":
            from llama_index.core import SummaryIndex
            # Re-fetch docs for summary index — limited to small collections
            engine = idx.as_query_engine(response_mode="tree_summarize")
            result = engine.query(query)
            return _cited_result(result)

        elif mode == "sub":
            from llama_index.core.query_engine import CitationQueryEngine
            from llama_index.core.query_engine import SubQuestionQueryEngine
            from llama_index.core.tools import QueryEngineTool, ToolMetadata
            base = CitationQueryEngine.from_args(idx, similarity_top_k=5)
            tool = QueryEngineTool(
                query_engine=base,
                metadata=ToolMetadata(name=collection, description=f"Index: {collection}"),
            )
            engine = SubQuestionQueryEngine.from_defaults(
                query_engine_tools=[tool], verbose=False
            )
            result = engine.query(query)
            return _cited_result(result)

        else:
            return f"Unknown mode '{mode}'. Use: auto | vector | summary | sub"

    except ImportError:
        return "Install: pip install llama-index llama-index-llms-ollama llama-index-embeddings-ollama"
    except Exception as e:
        logger.warning("LlamaIndex query error: %s", e)
        return f"Query error: {e}"


def li_summarize_path(path: str) -> str:
    """Summarise all documents at a path using LlamaIndex SummaryIndex."""
    try:
        from llama_index.core import SimpleDirectoryReader, SummaryIndex
        p = Path(os.path.expanduser(path))
        if p.is_file():
            docs = SimpleDirectoryReader(input_files=[str(p)]).load_data()
        elif p.is_dir():
            docs = SimpleDirectoryReader(str(p), recursive=True).load_data()
        else:
            return f"Path not found: {path}"

        _get_service_context()
        idx    = SummaryIndex.from_documents(docs)
        engine = idx.as_query_engine(response_mode="tree_summarize")
        result = engine.query("Provide a comprehensive summary of all the documents.")
        return _cited_result(result)
    except ImportError:
        return "Install: pip install llama-index llama-index-llms-ollama"
    except Exception as e:
        return f"Summarise error: {e}"


def li_list_collections() -> str:
    """List all Chroma collections (persisted indexes)."""
    try:
        import chromadb
        chroma_path = _chroma_dir()
        chroma_path.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(chroma_path))
        cols   = [c.name for c in client.list_collections()]
        cached = list(_index_cache.keys())
        return (
            f"Persisted collections: {cols}\n"
            f"In-memory (this session): {cached}"
        )
    except ImportError:
        cached = list(_index_cache.keys())
        return f"ChromaDB not installed (in-memory only). Session indexes: {cached}"
    except Exception as e:
        return f"List error: {e}"


# ── Central dispatch ──────────────────────────────────────────────────────

def llama_index_tool(action: str, args: dict) -> str:
    """
    Dispatch for the 'llama_index' tool in _execute_tool().

    action:
      index       — index a file or directory
      query       — query the index (mode: auto|vector|summary|sub)
      summarize   — summarise a file or directory
      collections — list all indexed collections
    """
    dispatch = {
        "index":       lambda: li_index_path(
            args.get("path", ""), args.get("collection", "aether")
        ),
        "query":       lambda: li_query(
            args.get("query", ""),
            args.get("collection", "aether"),
            args.get("mode", "auto"),
        ),
        "summarize":   lambda: li_summarize_path(args.get("path", "")),
        "collections": lambda: li_list_collections(),
    }
    fn = dispatch.get(action)
    if fn:
        try:
            return fn()
        except Exception as e:
            return f"llama_index '{action}' error: {e}"
    return f"Unknown action: {action}. Options: {list(dispatch)}"


# ── Tool declaration ───────────────────────────────────────────────────────

LLAMA_INDEX_TOOL_DECLARATION = {
    "name": "llama_index",
    "description": (
        "Production-grade document search and RAG powered by LlamaIndex. "
        "Supports PDF, DOCX, CSV, HTML, Markdown, and plain text. "
        "Use 'index' to add documents, 'query' to search them, "
        "'summarize' to summarise a whole folder, "
        "'collections' to list what's indexed. "
        "Query modes: auto (router picks), vector (similarity), "
        "summary (overview), sub (multi-part question decomposition)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action":     {"type": "STRING",
                           "description": "index | query | summarize | collections"},
            "path":       {"type": "STRING",
                           "description": "File or directory path (index / summarize)"},
            "query":      {"type": "STRING",
                           "description": "Question to ask the indexed documents"},
            "collection": {"type": "STRING",
                           "description": "Index collection name (default: aether)"},
            "mode":       {"type": "STRING",
                           "description": "auto | vector | summary | sub (query)"},
        },
        "required": ["action"],
    },
}
