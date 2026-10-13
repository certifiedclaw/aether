# Performance Notes

Living reference for optimization work on this codebase. Unlike
`CHANGELOG.md` / `IMPROVEMENTS_V*.md` (which log features + bug fixes
chronologically), this file tracks **perf-specific state**: what's been
audited, what's still unaudited, and the failure patterns to grep for.

Update this file whenever a perf pass touches a module — add it to the
"Audited" table, and add any new anti-pattern you find to the checklist
below if it's the kind of thing that could be lurking elsewhere too.

---

## Anti-pattern checklist

Grep for these before declaring a module "fine." Every one of them has
already been found at least once in this codebase.

| Pattern | Why it's a problem | Real example found here |
|---|---|---|
| A full rebuild/recompute called from a per-item hot path (`store()`, `add()`, a per-message handler) | O(corpus) work on every single call instead of O(1) amortized | `HybridMemory.store()` called `BM25Index.build()` (full re-tokenize) on every message |
| A directory/batch loop that calls a "do one thing + persist" function per item | Turns O(n) work into O(n²) — full re-embed/re-save after every single file | `RAGEngine.index_directory()` called `index_file()` which rebuilt + saved the whole index per file |
| A `while` / trim loop that recomputes a total over the full remaining collection each iteration | O(n²) when many items need dropping | `smart_context.compress_if_needed()` recomputed token count over all remaining messages on every drop |
| Config/file reads inside a function called many times per turn, with no mtime check | Disk I/O on every call instead of once | `llm_client._load_config()` — **fixed**, now cached + mtime-invalidated; use as the template for similar cases |
| A blocking network/subprocess call invoked directly inside a shared single-threaded command loop or UI callback | Freezes that loop for everyone else until the call returns | self-evaluator blocking UI return (fixed); cancel-phrase busy-guard blocking (fixed); `/selftest` running inline on the chat command loop (fixed — now `run_self_test_async`) |
| `sqlite3.connect()` opened fresh inside a function instead of reusing a stored connection | Connection setup overhead per call | Not yet found misused, but multiple modules (`hybrid_memory`, `semantic_memory`, `self_test`) touch sqlite — check any new one against this |
| A path built as a literal relative string (`"data/whatever"`) instead of resolved against `BASE_DIR` | Silently breaks / reads-writes the wrong file depending on cwd at launch | `self_test.py`'s old memory-DB path check — **fixed**, now resolves via `memory.config_manager.BASE_DIR` the same way `hybrid_memory`/`semantic_memory` do |
| A test/diagnostic routine with no per-test timeout, run serially | One stuck subsystem hangs the entire check | `self_test.py` — **fixed**, every test now runs under `ThreadPoolExecutor` with a timeout; independent tests run concurrently |

When auditing a new module, search it for: `.build(`, `for .* in os.walk`, `while .*:\s*$` loops that recompute a total, `sqlite3.connect`, `requests.get(`/`requests.post(`/`subprocess.run(` outside a `threading.Thread`, and any `"./` or bare relative path string.

---

## Audited modules

| Module | Status | Notes |
|---|---|---|
| `core/llm_client.py` | ✅ Fixed | `_load_config()` was re-reading disk every call; now cached + mtime-invalidated. `num_ctx` was never actually sent to Ollama — fixed. |
| `core/conversation_core.py` | ◐ Partial | Hosted Gemini/OpenAI API turns now use the larger cloud history budget instead of the local-model budget; full turn-processing path still needs live-model profiling. |
| `main.py` | ✅ Fixed (partial) | Self-evaluator backgrounded off the UI return path. Cancel-phrase busy-guard was blocking cancel commands — fixed. `/selftest` handler now uses `run_self_test_async` instead of blocking the chat command loop. |
| `ui.py` | ◐ Partial | Removed the 6 ms/character chat-log animation so complete messages display immediately; GPU metrics and orb rendering still need profiling under real UI load. |
| `self_test.py` | ✅ Rewritten | Per-test timeouts, concurrent stateless tests, real memory-DB path resolution + round-trip proof instead of a row count, new config/GPU/disk checks, non-blocking `run_self_test_async`. |
| `memory/hybrid_memory.py` | ✅ Fixed | `store()` was doing a full BM25 rebuild (re-tokenizing every stored memory) on every call — added `BM25Index.add_doc()` for true incremental updates. Verified score-identical to a full rebuild. |
| `memory/rag.py` | ✅ Fixed | `index_directory()` rebuilt + re-embedded + saved the whole index after every single file — now chunks everything first, rebuilds/embeds/saves once. Verified via call-count test (3 files → 1 rebuild, was 3). |
| `core/smart_context.py` | ✅ Fixed | Token-budget trim loop recomputed the full-list estimate every iteration (O(n²)) — now tracks a running total. Verified against tool-call pairing edge cases. |
| `agent/executor.py` | ⬜ Not yet audited | |
| `agent/planner.py` | ⬜ Not yet audited | |
| `agent/task_queue.py` | ⬜ Not yet audited | Prior session found + fixed a memory leak in `TaskQueue.cancel()` — worth a second pass given how much else in `agent/` hasn't been touched. |
| `core/observability.py` | ⬜ Not yet audited | |
| `core/task_scheduler.py` | ⬜ Not yet audited | |
| `core/plugins.py` / `core/plugin_watcher.py` | ⬜ Not yet audited | Hot-reload watcher polls the filesystem — worth checking poll interval + whether it re-scans everything vs. just changed files. |
| `memory/semantic_memory.py` | ⬜ Not yet audited | Same sqlite/embedding shape as `hybrid_memory.py` before the fix — check whether it has the same full-rebuild-on-write issue. |
| `actions/*.py` (browser_control, extra_tools, osint_tools, file_processor, game_updater, etc.) | ⬜ Not yet audited | Largest unaudited surface area (30–50KB files each) and nothing here has been perf-reviewed at all yet. |
| `dashboard/server.py` | ⬜ Not yet audited | |
| `integrations/*.py` (langchain_tools, llama_index_rag, oi_shell) | ⬜ Not yet audited | |

---

## How to use this file in a future session

1. Read the **Audited modules** table first — don't re-review anything marked ✅ unless the person mentions a specific symptom there.
2. Pick the next ⬜ from the table (biggest unaudited surface area currently: `actions/*.py`).
3. Run the anti-pattern checklist against it.
4. When you fix something, move its row to ✅ with a one-line "what was wrong / what changed" note, same style as above — not a full narrative, that's what `IMPROVEMENTS_V*.md` is for.
