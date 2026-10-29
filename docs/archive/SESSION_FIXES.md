# Session fixes (audit follow-up)

Applied against findings in the conversational-architecture audit. All items
below were verified via `py_compile`, the existing `tests/test_all_critical.py`
+ `tests/test_core.py` suite (65 passed, 2 skipped), `tests/stress_test.py`,
and targeted standalone regression checks.

## 1. History default (main.py)
`_prepare_conversation_messages` now reads `memory.chat_history_limit` with a
documented default of 100 (was silently 12 whenever the active config didn't
set the key explicitly).

## 2. `_conversation` race across threads (main.py)
Added `self._conversation_lock` (RLock). `_process_message` now serializes
the full `_process_message_inner` call, and `/clear` locks its
`_conversation.clear()`. STT, dashboard queue, proactive loop, and system
monitor all funnel through `_process_message`, so turns from different
threads can no longer interleave or land against the wrong user message.

## 3. `save_memory` swallowing the real answer (main.py)
Compound messages ("remember X, also what should I improve?") get one extra
round to answer before falling back to a canned "Noted." / "Got it, {name}."
— previously any all-silent tool round produced the canned ack immediately,
discarding a real pending question.

## 4. Hybrid/semantic memory recent-fallback (memory/hybrid_memory.py,
   memory/semantic_memory.py)
`_retrieve` no longer substitutes `get_recent()` (unrelated recent chat)
when BM25/semantic search finds no relevant candidates or the doc index is
empty — all of those paths now return `[]`, so no match means no injected
context instead of injecting whatever was chatted about last. Verified: a
query with zero BM25 term overlap, and a query against an empty store, both
inject nothing; a genuinely relevant query still retrieves correctly.
semantic_memory.py keeps its "no encoder installed at all" fallback (a real
capability gap, not a relevance miss) but no longer falls back to recent on
a search *exception*.

## 5. Tool-schema token budget defect (core/smart_context.py, main.py)
Added `estimate_tool_tokens()`. `_prepare_conversation_messages` now reserves
that many tokens out of the 3000-token compression budget before trimming
history, so "fits in budget" reflects the real request (messages + tool
schema), not just message content. Floor of 500 tokens with a logged warning
if the tool schema alone would otherwise eat nearly the whole budget.

## 6. Silent tool-loop exhaustion (main.py)
If all `MAX_TOOL_ROUNDS` (6) pass without ever producing a reply, the user
previously got total silence — no error, no log, no spoken response, and
nothing stored to memory. Now surfaces an explicit fallback message, logs a
warning, and stores the exchange.

## 7. Dead phone-audio path (dashboard/server.py, main.py)
Confirmed the dashboard client (`app.html` `doMic()`) already resamples to
16kHz mono and sends signed 16-bit PCM. Switched `_phone_audio_queue` from
`asyncio.Queue` to the same thread-safe `queue.Queue` pattern already used
for `_command_queue`, and added `_phone_audio_stt_loop` — a consumer thread
with its own `_VADBuffer` (separate from the local-mic loop's) that
transcribes and calls `_process_message`, same as the local mic path.
Least independently verified fix in this batch — no real phone-mic stream
was available to test end-to-end in this environment.

## Not yet addressed (deeper architecture, out of scope for this pass)
Per the audit's own roadmap: per-conversation actor replacing thread-direct
`_process_message` calls, a real Context Engine with summarization instead
of hard trimming, tool-subset routing (tier_router currently only does
hard-escalation, not tool gating), namespaced/expiring episodic memory, and
a working Gemini provider adapter with its own tests.
