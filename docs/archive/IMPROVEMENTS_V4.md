# Aether — v4 Improvements

## What's new in v4

### New top-level modules (previously unconnected, now wired in)

These files existed in the zip but were not imported or called from `main.py`.
v4 connects them all.

---

### 1. Crash Logger (`crash_logger.py`)

Writes a structured JSON report to `logs/crashes/` whenever any unhandled
exception occurs — either in tool execution or in the main message loop.

Each report includes:
- Exception type, message, and full traceback
- The user message that triggered it
- The tool being executed (if any)
- Python version, platform, and RAM/CPU usage at time of crash

**Wired in v4:**
- `_execute_tool` now calls `log_crash(e, tool=name, ...)` on any exception
- `_process_message` is now wrapped — any exception that escapes the inner
  loop is caught, logged, and surfaced to the user gracefully

**New chat commands:**
- `/crashes` — show the 5 most recent crash reports
- `"show crashes"` / `"crash report"` — same via natural language

---

### 2. Self-Test (`self_test.py`)

End-to-end system health check covering:
- Python version ≥ 3.10
- Ollama reachability
- LLM response (minimal prompt)
- SQLite memory DB
- Long-term memory read/write cycle
- Microphone and speaker device detection
- (If app is running) Hybrid memory, RAG engine, goals, plugins,
  evaluator, observability, loop guard

**Wired in v4:**
- `/selftest` chat command triggers `run_self_test(aether=self)`
- `"run self test"` / `"self test"` also trigger it
- Report printed to the log panel

---

### 3. Memory Tools (`memory_tools.py`)

Backup, export, import, prune, and stats for the memory system.

| Action | What it does |
|--------|-------------|
| `backup` | Zip snapshot of `memory.db`, `long_term.json`, `goals.json`, RAG index |
| `export` | Export DB memories to readable JSON |
| `import` | Restore from a previous export |
| `prune`  | Remove duplicates, too-short entries, and old entries from DB |
| `stats`  | Detailed stats across all memory systems |

**Wired in v4:**
- New `memory_tool` LLM tool declaration — the LLM can now call these
- Direct chat commands: `/backup`, `/memstats`, `/prune`, `"backup memory"`, etc.

---

### 4. Config Validator (`config_validator.py`)

Validates the full configuration before any subsystem loads.
Catches: missing keys, bad Ollama URL, unknown STT/TTS engines,
inaccessible paths, MCP config errors, bad memory thresholds.

**Wired in v4:**
- Called once in `main()` immediately after dependency install,
  before `AetherLocal` is constructed
- All issues (errors/warnings/info) are printed to the UI log
- Config errors are flagged clearly so users don't get cryptic crashes later

---

### 5. Two new LLM tools

| Tool | Description |
|------|-------------|
| `memory_tool` | Backup / export / import / prune / stats via voice or text |
| `crash_report` | Show recent crash logs via voice or text |

### 6. Evaluator — `quick_check` now used as pre-filter

`Evaluator.quick_check()` existed but was never called. It's now run before
the full LLM evaluation: if the response already looks like an obvious failure
(contains phrases like "I cannot", "error occurred", etc.) the fast heuristic
catches it immediately without spending an LLM call on evaluation.

---

### 7. `call_llm_text` — tier routing

Added `tier=` parameter (`"fast"` / `"deep"` / `"default"`).

- `"fast"` — 30s timeout, 300 tokens (classification, error analysis, quick decisions)
- `"deep"` — 180s timeout, 1200 tokens (code generation, planning, summarisation)
- `"default"` — 120s timeout, 600 tokens (everything else)

The top-level `executor.py`, `planner.py`, and `error_handler.py` were already
using `tier=` calls; now `call_llm_text` actually honours them.

---

### 8. Agent files merged

The top-level `executor.py`, `planner.py`, and `error_handler.py` (which had been
updated for v4 but left outside `agent/`) have been merged into `agent/`.
Improvements included: explicit `{step_N}` placeholder injection between plan steps,
generic auto-inject for any content-bearing parameter, `extra_tool` dispatch,
and corrected `generated_code` guard rule.

---

| File | Fix |
|------|-----|
| `self_test.py` | `Any` was used in `_timed()` return type but not imported — moved to top-level imports |
| `config_validator.py` | URL key lookup now checks `ollama_url` and `llm_url` first, matching what `llm_client.py` and `config.example.json` actually use |
| `core/safety.py` | Removed `"not found"` and `"⚠️"` from `verify_tool_result` failure phrases — both appear in valid success responses, causing false negatives |
| `config/config.example.json` | Added all missing keys: `stt_language`, `tts_voice`, `llm_provider`, `llm_url`, `memory.*`, `logging.*`, `rag.*`, `rag.watch_dirs` |
| `file_watcher.py` | Fixed inverted first-scan logic — files were being indexed on first discovery (flooding RAG at startup) instead of only when modified |
| `core/llm_client.py` | Added `tier=` parameter (`"fast"` / `"deep"` / `"default"`) with appropriate timeout and `num_predict` overrides |
| `agent/executor.py` | Merged from improved top-level v4 version: `tier=` calls, explicit `{step_N}` placeholder injection, generic auto-inject for any content-bearing param, `extra_tool` dispatch |
| `agent/planner.py` | Merged from improved top-level v4 version: corrected `generated_code` rule wording, `extra_tool` / `image_gen` / `email_tool` declarations |
| `agent/error_handler.py` | Merged from improved top-level v4 version: `tier=` calls |

---

## New chat commands (v4 total)

| Command | Action |
|---------|--------|
| `/selftest` or `"run self test"` | Run full system self-test |
| `/crashes` or `"show crashes"` | Show recent crash logs |
| `/backup` or `"backup memory"` | Snapshot memory to zip |
| `/memstats` or `"memory stats"` | Show memory system stats |
| `/prune` or `"prune memory"` | Remove duplicate/old memories |
| `/status` / `"status"` | (existing) Full system diagnostic |
| `/clear` | (existing) Clear chat history |

---

## New runtime directories

```
Mark-XL/
└── logs/
    └── crashes/    ← JSON crash reports (auto-created, rotates at 50 files)
```

---

## v4 — Round 2 fixes

### `run_extra_tool` — written and wired in

`actions/extra_tools.py` had 45 individual utility functions but no dispatch
entry point. `agent/executor.py` called `run_extra_tool(action, params)` which
didn't exist, causing a silent `ImportError` on any `extra_tool` plan step.

Written: a full `run_extra_tool` dispatch table mapping every action name the
planner uses to the correct function, including name-normalisation for aliases
(`wifi_list` → `list_wifi_networks`, `auto_type` → `type_text`, `qr_code` →
`generate_qr`, etc.) and the two clipboard actions (`clipboard_read`,
`clipboard_write`) that were declared in the planner but had no implementation.

Also added `extra_tool` to `TOOL_DECLARATIONS` in `main.py` so the LLM can
call it directly from the main conversation loop (not only from multi-step plans).

### `_NEEDS_LLM_ROUND` — `code_helper` and `dev_agent` added

These tools return raw generated code/output; without an LLM synthesis round
the response was being sent directly to the user as an unformatted dump.

### CRLF line endings — normalised to LF across all files

18 Python files and `core/prompt.txt` had Windows CRLF line endings, which
cause `SyntaxError` on some Linux/macOS shells when files are exec'd or piped.
All normalised to LF.

### `requirements.txt` — added `qrcode[pil]` and `wikipedia-api`

Both are used by `extra_tools.py` actions but were missing from the dependency list.

### Additional bug fixes

| File | Fix |
|------|-----|
| `file_watcher.py` | *(previous round)* First-scan logic was inverted — all files were indexed at startup |
| `_NEEDS_LLM_ROUND` | Added `code_helper`, `dev_agent` — raw output now goes through an LLM synthesis round before being spoken/displayed |
