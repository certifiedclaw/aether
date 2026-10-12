"""
integrations/langchain_tools.py — Aether × LangChain

Integrates LangChain as an installed library. Exposes a thin adapter so
_execute_tool() in main.py can call LangChain chains/agents with one line.

Features pulled from langchain-ai/langchain:
  • DuckDuckGoSearchRun    — web search tool (no API key needed)
  • WikipediaQueryRun      — Wikipedia lookup
  • summarize_chain        — map-reduce summarisation for long documents
  • ReAct agent            — LangChain ReAct loop (used as a sub-agent for
                             complex multi-hop research tasks)
  • ConversationSummaryBufferMemory — smart context compression that
                             summarises old turns instead of dropping them
                             (replaces the basic compress_if_needed in smart_context.py)

None of these replace Aether's own memory or RAG — they augment it.
The LangChain Ollama LLM is wired to the same Ollama server the rest of
the system uses, so there's no separate model to manage.

Requires: pip install langchain langchain-community langchain-ollama duckduckgo-search wikipedia
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)


# ── Lazy Ollama LLM (shared across all LangChain calls) ───────────────────

@lru_cache(maxsize=1)
def _get_llm(model: str = "qwen3:8b", base_url: str = "http://localhost:11434"):
    try:
        from langchain_ollama import OllamaLLM
        return OllamaLLM(model=model, base_url=base_url, temperature=0.4)
    except ImportError:
        try:
            from langchain_community.llms import Ollama
            return Ollama(model=model, base_url=base_url, temperature=0.4)
        except ImportError as e:
            raise ImportError(
                "Install LangChain Ollama: pip install langchain-ollama"
            ) from e


def _llm_from_config() -> Any:
    """Get Ollama LLM using the project config."""
    try:
        from memory.config_manager import get_mark_config
        cfg = get_mark_config()
        model    = cfg.get("llm.model", cfg.get("llm_model", "qwen3:8b"))
        base_url = cfg.ollama_url
        return _get_llm(model, base_url)
    except Exception:
        return _get_llm()


# ── Web search ────────────────────────────────────────────────────────────

def lc_web_search(query: str, num_results: int = 5) -> str:
    """
    DuckDuckGo web search via LangChain tool.
    No API key required. Returns a clean text summary.
    """
    try:
        from langchain_community.tools import DuckDuckGoSearchRun
        tool = DuckDuckGoSearchRun()
        return tool.run(query)
    except ImportError:
        return "Install: pip install langchain-community duckduckgo-search"
    except Exception as e:
        return f"Web search error: {e}"


# ── Wikipedia ─────────────────────────────────────────────────────────────

def lc_wikipedia(query: str, sentences: int = 5) -> str:
    """Quick Wikipedia lookup."""
    try:
        from langchain_community.tools import WikipediaQueryRun
        from langchain_community.utilities import WikipediaAPIWrapper
        wiki = WikipediaQueryRun(api_wrapper=WikipediaAPIWrapper(
            top_k_results=1, doc_content_chars_max=sentences * 200
        ))
        return wiki.run(query)
    except ImportError:
        return "Install: pip install langchain-community wikipedia"
    except Exception as e:
        return f"Wikipedia error: {e}"


# ── Summarisation ─────────────────────────────────────────────────────────

def lc_summarize(text: str, style: str = "concise") -> str:
    """
    Map-reduce summarisation using LangChain.
    Works on arbitrarily long text by splitting into chunks.
    style: concise | bullets | detailed
    """
    try:
        from langchain.text_splitter import RecursiveCharacterTextSplitter
        from langchain.chains.summarize import load_summarize_chain
        from langchain.docstore.document import Document
        from langchain.prompts import PromptTemplate

        llm = _llm_from_config()

        style_map = {
            "concise":  "Write a concise 2-3 sentence summary.",
            "bullets":  "Summarise as 5 bullet points.",
            "detailed": "Write a detailed summary covering all key points.",
        }
        instruction = style_map.get(style, style_map["concise"])

        map_prompt = PromptTemplate(
            input_variables=["text"],
            template=f"Summarise the following text.\n{{text}}\n\n{instruction}",
        )
        combine_prompt = PromptTemplate(
            input_variables=["text"],
            template=f"Combine these summaries into a final summary.\n{{text}}\n\n{instruction}",
        )

        splitter = RecursiveCharacterTextSplitter(chunk_size=3000, chunk_overlap=200)
        docs     = [Document(page_content=c) for c in splitter.split_text(text)]

        if len(docs) == 1:
            chain_type = "stuff"
        else:
            chain_type = "map_reduce"

        chain = load_summarize_chain(
            llm, chain_type=chain_type,
            map_prompt=map_prompt if chain_type == "map_reduce" else None,
            combine_prompt=combine_prompt,
            verbose=False,
        )
        return chain.invoke({"input_documents": docs})["output_text"]

    except ImportError:
        return "Install: pip install langchain"
    except Exception as e:
        return f"Summarisation error: {e}"


# ── ReAct research agent ──────────────────────────────────────────────────

def lc_research_agent(task: str, extra_context: str = "") -> str:
    """
    LangChain ReAct agent for multi-hop research tasks.
    Equipped with web search + Wikipedia. Best for tasks like:
    "find the latest papers on X and summarise their findings".

    Runs as a single synchronous call — does NOT re-enter Aether's
    tool loop.  Max iterations are intentionally low so the outer loop
    budget isn't consumed.
    """
    try:
        from langchain.agents import AgentExecutor, create_react_agent
        from langchain_community.tools import DuckDuckGoSearchRun, WikipediaQueryRun
        from langchain_community.utilities import WikipediaAPIWrapper
        from langchain.prompts import PromptTemplate
        from langchain import hub

        llm   = _llm_from_config()
        try:
            from memory.config_manager import get_mark_config
            # Hard cap at 4: leaves budget for Aether's own tool rounds
            _max_iter = min(int(get_mark_config().get("langchain.max_research_steps", 4)), 4)
        except Exception:
            _max_iter = 4
        tools = [
            DuckDuckGoSearchRun(name="web_search",
                                description="Search the web for current information"),
            WikipediaQueryRun(
                name="wikipedia",
                description="Look up factual information on Wikipedia",
                api_wrapper=WikipediaAPIWrapper(top_k_results=2,
                                                doc_content_chars_max=2000),
            ),
        ]

        # Use the standard ReAct prompt from LangChain hub, fall back to inline
        try:
            prompt = hub.pull("hwchase17/react")
        except Exception:
            prompt = PromptTemplate.from_template(
                "Answer the following question using the tools provided.\n\n"
                "Tools: {tools}\n\nTool names: {tool_names}\n\n"
                "Question: {input}\n\nScratchpad:\n{agent_scratchpad}"
            )

        agent    = create_react_agent(llm, tools, prompt)
        executor = AgentExecutor(
            agent=agent, tools=tools,
            max_iterations=_max_iter, verbose=False,
            handle_parsing_errors=True,
        )
        full_task = f"{extra_context}\n\n{task}".strip() if extra_context else task
        result    = executor.invoke({"input": full_task})
        return result.get("output", str(result))

    except ImportError:
        return "Install: pip install langchain langchain-community duckduckgo-search wikipedia"
    except Exception as e:
        logger.warning("LangChain ReAct agent error: %s", e)
        return f"Research agent error: {e}"


# ── Smart context compression (replaces smart_context.compress_if_needed) ─

def lc_compress_history(messages: list[dict], max_tokens: int = 3000,
                         router=None) -> list[dict]:
    """
    ConversationSummaryBufferMemory-style compression.
    When the conversation exceeds max_tokens, the oldest turns are
    summarised by the LLM and replaced with a single [Summary] system message.
    Falls back to simple truncation if LangChain isn't available.
    """
    _CHARS_PER_TOKEN = 4
    total = sum(len(str(m.get("content", ""))) for m in messages)
    if total // _CHARS_PER_TOKEN <= max_tokens:
        return messages  # within budget, no action needed

    try:
        from langchain.memory import ConversationSummaryBufferMemory
        from langchain.schema import HumanMessage, AIMessage

        llm = _llm_from_config()
        mem = ConversationSummaryBufferMemory(
            llm=llm, max_token_limit=max_tokens // 2,
            return_messages=True,
        )
        # Replay messages into the buffer memory
        system = [m for m in messages if m["role"] == "system"]
        turns  = [m for m in messages if m["role"] != "system"]
        for i in range(0, len(turns) - 1, 2):
            user = turns[i].get("content", "")
            asst = turns[i + 1].get("content", "") if i + 1 < len(turns) else ""
            mem.save_context({"input": user}, {"output": asst})

        compressed = mem.load_memory_variables({}).get("history", [])
        # Reconstruct message list: system + summary + latest turn
        summary_text = " ".join(
            m.content if hasattr(m, "content") else str(m)
            for m in (compressed if isinstance(compressed, list) else [compressed])
        )
        rebuilt = system + [
            {"role": "system", "content": f"[Conversation summary]\n{summary_text}"}
        ]
        # Always keep the very last user message
        last_user = next((m for m in reversed(turns) if m["role"] == "user"), None)
        if last_user:
            rebuilt.append(last_user)
        return rebuilt

    except ImportError:
        # Fallback: deterministic truncation. Delegates to smart_context so the
        # newest message (the current user request) is never dropped — the old
        # inline loop popped it when the system prompt alone exceeded the budget.
        from core.smart_context import compress_if_needed
        return compress_if_needed(messages, token_budget=max_tokens)
    except Exception as e:
        logger.debug("LangChain compression error: %s", e)
        return messages


# ── Document loader (for files → RAG via LangChain loaders) ──────────────

def lc_load_document(path: str) -> str:
    """
    Load a document using LangChain's loaders (PDF, DOCX, CSV, HTML, etc.)
    and return its text content for indexing into Aether's RAG engine.
    """
    import os
    from pathlib import Path as _Path
    p   = _Path(os.path.expanduser(path))
    ext = p.suffix.lower()
    try:
        if ext == ".pdf":
            from langchain_community.document_loaders import PyPDFLoader
            docs = PyPDFLoader(str(p)).load()
        elif ext in (".docx", ".doc"):
            from langchain_community.document_loaders import Docx2txtLoader
            docs = Docx2txtLoader(str(p)).load()
        elif ext == ".csv":
            from langchain_community.document_loaders import CSVLoader
            docs = CSVLoader(str(p)).load()
        elif ext in (".html", ".htm"):
            from langchain_community.document_loaders import UnstructuredHTMLLoader
            docs = UnstructuredHTMLLoader(str(p)).load()
        elif ext == ".md":
            from langchain_community.document_loaders import TextLoader
            docs = TextLoader(str(p), encoding="utf-8").load()
        else:
            from langchain_community.document_loaders import TextLoader
            docs = TextLoader(str(p), encoding="utf-8", autodetect_encoding=True).load()
        return "\n\n".join(d.page_content for d in docs)
    except ImportError:
        return "Install: pip install langchain-community"
    except Exception as e:
        return f"Load error: {e}"


# ── Main dispatch ─────────────────────────────────────────────────────────

def langchain_tool(action: str, args: dict) -> str:
    """
    Central dispatch for the 'langchain' tool in _execute_tool().

    action:
      web_search   — DuckDuckGo search
      wikipedia    — Wikipedia lookup
      summarize    — map-reduce summarisation
      research     — ReAct multi-hop agent
      load_doc     — load document text via LangChain loaders
    """
    dispatch = {
        "web_search": lambda: lc_web_search(
            args.get("query", ""),
            int(args.get("num_results", 5))
        ),
        "wikipedia": lambda: lc_wikipedia(
            args.get("query", ""),
            int(args.get("sentences", 5))
        ),
        "summarize": lambda: lc_summarize(
            args.get("text", ""),
            args.get("style", "concise")
        ),
        "research": lambda: lc_research_agent(
            args.get("task", ""),
            args.get("context", "")
        ),
        "load_doc": lambda: lc_load_document(args.get("path", "")),
    }
    fn = dispatch.get(action)
    if fn:
        try:
            return fn()
        except Exception as e:
            return f"LangChain '{action}' error: {e}"
    return f"Unknown LangChain action: {action}. Options: {list(dispatch)}"


# ── Tool declaration (add to TOOL_DECLARATIONS in main.py) ────────────────

LANGCHAIN_TOOL_DECLARATION = {
    "name": "langchain",
    "description": (
        "Advanced AI tools powered by LangChain: web search, Wikipedia lookup, "
        "long document summarisation (map-reduce), multi-hop research agent "
        "(ReAct), and document loading. Use 'research' for complex multi-step "
        "information gathering tasks that need several search iterations."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action":      {"type": "STRING",
                            "description": "web_search | wikipedia | summarize | research | load_doc"},
            "query":       {"type": "STRING", "description": "Search query (web_search / wikipedia)"},
            "task":        {"type": "STRING", "description": "Research task description (research)"},
            "text":        {"type": "STRING", "description": "Text to summarise (summarize)"},
            "style":       {"type": "STRING", "description": "concise | bullets | detailed (summarize)"},
            "path":        {"type": "STRING", "description": "File path to load (load_doc)"},
            "context":     {"type": "STRING", "description": "Extra context for research agent"},
            "num_results": {"type": "INTEGER","description": "Number of search results"},
            "sentences":   {"type": "INTEGER","description": "Wikipedia summary length"},
        },
        "required": ["action"],
    },
}
