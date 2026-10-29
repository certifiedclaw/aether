# Aether — v3 Improvements

## What's new in v3

### integrations/ — new module

Three external frameworks integrated as optional library dependencies.
All are lazy-loaded with graceful fallback messages if not installed.

---

### 1. LangChain (`integrations/langchain_tools.py`)
`pip install langchain langchain-community langchain-ollama duckduckgo-search wikipedia`

| Action | What it does |
|--------|-------------|
| `web_search` | DuckDuckGo search — no API key needed |
| `wikipedia` | Wikipedia lookup |
| `summarize` | Map-reduce summarisation for arbitrarily long documents |
| `research` | ReAct multi-hop agent — searches web iteratively to answer complex questions |
| `load_doc` | Load PDF / DOCX / CSV / HTML / MD into text for RAG indexing |

**Also wired:** `lc_compress_history` now replaces the basic `compress_if_needed`
in the main conversation loop. Old turns are summarised by the LLM instead of
being dropped, so Aether retains the meaning of long conversations within the
token budget.

---

### 2. Open Interpreter (`integrations/oi_shell.py`)
`pip install open-interpreter`

Natural language computer control. The LLM writes Python/shell/JS code and
executes it locally to accomplish tasks that can't be done with fixed tools:
- Process and plot data files
- Batch-rename / move / zip files
- Install packages and configure software
- Automate anything the shell can do

| Action | What it does |
|--------|-------------|
| `run` | Run a single task (isolated) |
| `chat` | Multi-turn session (remembers variables, files) |
| `reset` | Clear session state |
| `status` | Show current config |

**Safe mode:** defaults to `"ask"` — OI confirms before executing.
Set `oi.safe_mode: "off"` in config.yaml for fully autonomous mode.
Uses the same Ollama model and URL as the rest of Aether.

---

### 3. LlamaIndex (`integrations/llama_index_rag.py`)
`pip install llama-index llama-index-llms-ollama llama-index-embeddings-ollama`

Production-grade RAG. Better than the built-in RAG engine for:
- Large document collections
- Complex multi-part questions (SubQuestion decomposition)
- Mixed document types in one index

| Action | What it does |
|--------|-------------|
| `index` | Index a file or directory (PDF, DOCX, CSV, HTML, MD, TXT) |
| `query` | Search the index (mode: auto / vector / summary / sub) |
| `summarize` | Summarise an entire folder with tree_summarize |
| `collections` | List all named indexes |

Optional: add `chromadb` + `llama-index-vector-stores-chroma` for persistent
indexes that survive restarts.

---

## Tool routing cheat-sheet

| Task | Tool |
|------|------|
| Quick web question | `langchain(action=web_search)` |
| Multi-step research | `langchain(action=research)` |
| Summarise a long doc | `langchain(action=summarize)` or `llama_index(action=summarize)` |
| Run code / edit files | `open_interpreter(action=run)` |
| Search indexed documents | `llama_index(action=query)` |
| Add docs to knowledge base | `llama_index(action=index)` |

---

## Install all v3 integrations at once

```bash
pip install langchain langchain-community langchain-ollama \
            duckduckgo-search wikipedia \
            open-interpreter \
            llama-index llama-index-llms-ollama llama-index-embeddings-ollama
```
