markdown
# Aether — Development Guide

This guide is for developers (human or AI) extending Aether. It covers standards, project layout, and how to add new features.

## 1. Project Layout
aether/
├── actions/ # Tool implementations (open_app, browser_control, etc.)
├── agent/ # Planner, Executor, Error Handler, Task Queue
├── config/ # Configuration schema and init.py helpers
├── core/ # Core systems: LLM client, STT, TTS, Safety, Observability
├── dashboard/ # FastAPI web server + static assets
├── integrations/ # Optional: LangChain, LlamaIndex, Open Interpreter wrappers
├── memory/ # Memory engines (Hybrid, RAG, Semantic, Config Manager)
├── security/ # Remote access security (Capabilities, Tokens, Policy)
├── tools/ # Maintenance scripts (health checks, project state, roadmap)
├── data/ # Runtime data (DB, RAG index, backups, screenshots)
├── logs/ # Rotating logs (aether.log, crashes/, security.log)
├── main.py # Entry point – orchestrates the UI and AetherLocal
├── ui.py # PyQt6 interface – AetherOrb, panels, menus
├── evaluator.py # Self-evaluation system (scores responses)
├── self_test.py # End-to-end system health check
└── version.py # Single source of truth for APP_NAME and version

text

## 2. Coding Standards

(Enforced by `tools/maintenance/health_check.py`)

- **No `print()`**: Use `logging.debug()` or `observability.log()` for diagnostics.
- **No bare `except:`**: Always catch `Exception` (or specific exceptions) and log the error.
- **Function length**: Maximum 80 lines. If a function exceeds this, split it into smaller helper functions.
- **File length**: Maximum 600 lines. If a file exceeds this, split it into logical modules (e.g., `osint_tools.py` → `osint/whois.py`, `osint/dns.py`).
- **Type Hints**: Use modern Python type hints (e.g., `def foo(bar: str) -> dict[str, Any]:`).

## 3. Adding a New Tool

1. **Implement the function** in an existing or new file under `actions/`.
   - Must accept a `parameters: dict` and return a `str` result.
   - Example:
     ```python
     def my_new_tool(parameters: dict) -> str:
         param = parameters.get("my_arg", "default")
         result = do_something(param)
         return f"Done: {result}"
Add the declaration to TOOL_DECLARATIONS in main.py:

python
{
    "name": "my_new_tool",
    "description": "Does something useful.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "my_arg": {"type": "STRING", "description": "The argument to process"}
        },
        "required": ["my_arg"]
    }
}
Add the dispatch to _execute_tool() in main.py:

python
elif name == "my_new_tool":
    result = my_new_tool(args)
Update tests: Add a test in tests/test_all_critical.py to ensure the dispatch routes correctly.

4. Memory System
Long-term: memory/long_term.json stores facts with categories (identity, preferences, projects). Use save_memory tool to write.

Episodic: SQLite DB at data/memory.db. Handled by HybridMemory (BM25 + dense + reranker).

RAG: RAGEngine indexes documents. To add a new file type, extend _read() in memory/rag.py.

5. Testing
Unit tests: Place in tests/. Use pytest.

Current test files:

test_core.py – Parser and dispatcher (all passing).

test_all_critical.py – Covers config, safety, security, memory, LLM retries.

Known failing tests (as of this writing):

test_config_adapter_dotted_lookup – see FIXES section below.
test_security_rate_limiting – threshold mismatch.
test_pin_consumption_and_expiry – mock time issue.
6. Fixing the 3 Critical Failing Tests
A. Config Adapter (_MarkConfigAdapter.get())
File: memory/config_manager.py, line ~60.

Bug: The method checks top = key.split(".")[0] in self._raw instead of key in self._raw.

Fix: Change the condition to if key in self._raw: so dotted keys like "memory.chat_history_limit" are found.

B. Security Rate Limiting
File: security/tokens.py.

Bug: _RATE_LIMIT_MAX is set to 5, but the test fails after 4 attempts. Either the constant is actually 4, or _prune_attempts() is removing entries incorrectly.

Fix: Verify _RATE_LIMIT_MAX = 5. Change the test to expect False after 4 attempts. If the test still fails, examine is_rate_limited() to ensure it uses len(self._attempts[ip]) >= self._RATE_LIMIT_MAX.

C. PIN Expiry Mock
File: tests/test_all_critical.py.

Bug: Mocking time.time inside a dict comprehension causes a TypeError.

Fix: Avoid mocking. Directly manipulate mgr._pending[pin2] = time.time() - 1 to force expiry.

7. Running Maintenance Checks
Regenerate all health reports and project state:

bash
python -m tools.maintenance.run_all
Add a handoff entry before finishing a session:

bash
python -m tools.maintenance.handoff add \
  --by "Your Name/Model" \
  --summary "What you did" \
  --files "files/touched" \
  --next "What the next session should do"