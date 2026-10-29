# Aether — v2 Upgrade Notes

## Six gaps addressed

### 1. Memory quality — `memory/hybrid_memory.py`
Replaced pure-dense retrieval with **three-stage hybrid**:
- **Stage 1 BM25** — fast keyword scoring, zero extra deps, catches exact names/commands
- **Stage 2 Dense** — sentence-transformer semantic similarity
- **Stage 3 Cross-encoder reranker** — re-scores top candidates for final precision (optional, falls back gracefully)

Upgrade path: `HybridMemory` is API-compatible with the old `MemoryEngine` — `main.py` uses it automatically when available and falls back to `MemoryEngine` otherwise.

Upgraded `memory/rag.py` uses the same BM25+dense hybrid for document retrieval (previously pure-dense).

---

### 2. Evaluation — `evaluator.py` (already existed, now wired in)
The evaluator existed in the zip but was never called. It is now invoked at the end of every `_process_message` turn that produced tool results.

On failure (score < 0.6):
- `obs.count("eval.failed")` is recorded
- `obs.log("eval_fail", ...)` emits a structured event to `logs/events.jsonl`
- A warning is written to the UI log

---

### 3. State management — `core/goal_state.py`
New file. Persistent goals survive session restarts:
- Goals stored as JSON to `data/goals.json`
- Injected into system prompt so the LLM always knows active goals
- LLM can create/update/complete/cancel goals via `goal_manager` tool
- Priority queue: urgent → high → normal → low
- Sub-tasks per goal with completion tracking

---

### 4. Autonomy — `core/task_scheduler.py` (already existed, now enhanced)
No changes needed — already wired. File watcher adds autonomous background indexing: drop a file into a watched directory and it's indexed into RAG automatically.

---

### 5. Permissions — `core/safety.py` (already existed)
No changes. The existing v2 safety layer is correct — `run_shell` always requires confirmation, path ACL enforced.

---

### 6. Observability — `core/observability.py`
New file. Three components:
- **StructuredLogger** — writes `logs/events.jsonl` (newline-delimited JSON, parseable by any log aggregator)
- **MetricsCollector** — in-memory counters and timing histograms (`tool.calls`, `eval.passed`, `loop_guard.blocked`, etc.)
- **Tracer** — lightweight span tracing around every tool call; optional OpenTelemetry export if SDK installed

Access the report in chat: type `obs report` (routes to `obs_report` tool).

---

## New files

| File | Purpose |
|------|---------|
| `memory/hybrid_memory.py` | BM25 + dense + cross-encoder retrieval |
| `core/observability.py`   | Structured logs, metrics, spans |
| `core/goal_state.py`      | Persistent goal management |
| `memory/rag.py`           | Upgraded to hybrid BM25+dense (replaces pure-dense) |

## Changed files

| File | Change |
|------|--------|
| `main.py` | Loop guard reset per request; observability spans around tool calls; evaluator called after each turn; hybrid memory preferred over plain semantic memory; goal state injected into system prompt; goal_manager + obs_report tool handlers added; all v2 systems started in `_init_new_subsystems()` |
| `requirements.txt` | Added `python-docx` for .docx RAG indexing |

## New tool available to the LLM

| Tool | Trigger phrases |
|------|----------------|
| `goal_manager` | "remember my goal to...", "track project X", "what are my active goals", "mark goal abc123 done" |
| `obs_report` | "show metrics", "observability report", "how many tool calls today" |
