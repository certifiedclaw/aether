# Aether — v6 Fixes

---

## 🔴 Bug fixes

### 1. `_MarkConfigAdapter.get()` silently ignored all dotted config keys
The adapter's `get()` checked whether the first segment of the key (`"memory"`)
existed in the raw dict. Since the JSON file stores keys as
`"memory.chat_history_limit"` (a literal dotted string), the segment `"memory"`
was never found, so the method always fell through to the built-in defaults —
ignoring whatever the user actually configured.

Every subsystem that uses the config adapter was affected: `HybridMemory`,
`SemanticMemory`, `RAGEngine`, `SafetyLayer`, etc. For example, setting
`"memory.chat_history_limit": 50` in `api_keys.json` had zero effect.

**Fix:** Replaced the broken segment-check with a direct `if key in self._raw`
lookup. Raw config values now always win over defaults.

---

### 2. `_MarkConfigAdapter` singleton not invalidated on reconfigure
When the user changed settings via **⚙ Configure**, `_do_reconfigure()` updated
`self._config` (the raw dict) but the config_manager singleton
(`_mark_config_instance`) was still holding the old `_MarkConfigAdapter`.
All subsystems that called `get_mark_config()` (memory, RAG, plugins, safety)
continued using the pre-reconfigure values for the rest of the session.

**Fix:** Added `invalidate_config_cache()` to `config_manager.py` and called it
at the start of `_do_reconfigure()`.

---

### 3. `call_llm_text` always used the Ollama endpoint, ignoring `llm_provider`
The planner, executor, error handler, code helper, dev agent, evaluator, and
context compressor all use `call_llm_text` for internal text generation.
When `llm_provider` was set to `"openai"` (LM Studio, LocalAI, Jan, etc.),
every one of those internal calls still hit `http://…/api/chat` (the Ollama
endpoint) and failed.

**Fix:** `call_llm_text` now checks `get_llm_provider()` and routes to
`/v1/chat/completions` for OpenAI-compatible backends, mirroring what
`call_llm` and `call_llm_stream` already did.

---

### 4. `error_handler.generate_fix()` fallback used `"generated_code"` tool name
When fix generation failed, the fallback step was emitted with
`tool: "generated_code"` — a tool name that is explicitly blocked by the
planner and executor (it exists only as an internal code-path, not a
dispatchable tool). The executor fell through to the unknown-tool branch.

**Fix:** Changed the fallback to `tool: "code_helper"` with
`action: "run"`, which is a real dispatchable tool.

Also fixed the same wrong replacement in `planner.create_plan()` and
`planner.replan()`: both guards were replacing `generated_code` steps with
`web_search`. A `generated_code` step is a code execution task, not a search —
the correct replacement is `code_helper(action="run")`.

---

### 5. `HybridMemory.store()` rebuilt the BM25 index on every single write
After every exchange, `store()` called `self._bm25.build(self._doc_texts)`,
which iterates the entire document list and recomputes term frequencies and
document frequencies from scratch. With 500+ memories this was O(n) per
message turn.

**Fix:** `store()` now sets a `_bm25_dirty` flag instead of rebuilding.
`_retrieve()` checks the flag and rebuilds lazily on the first query after
new documents were added — typically once per turn at most, and not at all
on turns with no memory writes.

---

### 6. `warmup_model()` in `reconfigure()` called without system prompt
When the user changed the LLM model via Configure, `warmup_model()` was called
without a `system_prompt` argument, so Ollama's KV prefix cache was not primed
with the Aether protocol text. The first real request after a model change would
incur full prompt evaluation latency instead of the near-zero cached latency.

**Fix:** `_do_reconfigure()` now loads the static system prompt and passes it to
`warmup_model(system_prompt=...)`, matching what the startup warmup path does.

---

## 🟡 Quality / reliability

### 7. Agent task failures not crash-logged
When an `agent_task` step threw an unhandled exception, `task_queue._run_task()`
caught it, printed it, and set the task status to FAILED — but never wrote a
structured crash report to `logs/crashes/`.

**Fix:** The `except` block in `_run_task()` now calls `log_crash()` with the
task ID and goal for context, matching the coverage added for the main
message loop and STT threads in v4/v5.

---

### 8. `smart_context` summarisation was permanently disabled
`ENABLE_SUMMARIZE = False` was hard-coded and the `router` parameter was never
passed by any caller, meaning dropped turns were silently discarded with no
summary. In a long session the LLM would lose all context from early turns.

**Fix:** Rewrote `compress_if_needed()` to call `call_llm_text()` directly
(no router needed) and always produce a 1-2 sentence summary of the dropped
turns, injected as a system message. The `router` parameter is kept for API
compatibility but ignored.

---

## Files changed

| File | Change |
|------|--------|
| `memory/config_manager.py` | Fix 1 — `get()` dotted-key lookup; Fix 2 — `invalidate_config_cache()` |
| `main.py` | Fix 2 — call `invalidate_config_cache()` in `_do_reconfigure()`; Fix 6 — pass system prompt to `warmup_model()` |
| `core/llm_client.py` | Fix 3 — `call_llm_text` OpenAI provider routing |
| `agent/error_handler.py` | Fix 4 — fallback tool `generated_code` → `code_helper` |
| `agent/planner.py` | Fix 4b — `generated_code` guard → `code_helper` in both `create_plan()` and `replan()` |
| `memory/hybrid_memory.py` | Fix 5 — lazy BM25 rebuild via `_bm25_dirty` flag |
| `agent/task_queue.py` | Fix 7 — crash-log agent task failures |
| `core/smart_context.py` | Fix 8 — enable summarisation of dropped turns |
