# Aether bug fixes

Drop each file into its matching path in your project root (overwrite the
existing file). Paths mirror your project structure:

    main.py                 → aether/main.py
    core/llm_client.py      → aether/core/llm_client.py
    core/smart_context.py   → aether/core/smart_context.py
    agent/executor.py       → aether/agent/executor.py

## main.py

1. **UI left stuck on "Thinking" after any LLM error** (the main cause of
   the reported hang). In `_process_message_inner`, the streaming tool-call
   loop set state to `THINKING`, then called `call_llm_stream(...)`. If that
   raised a `RuntimeError` — connection drop, timeout, HTTP error, model not
   found, anything — the handler spoke the error and returned immediately,
   *without* ever resetting the UI state back to `LISTENING`. Every other
   exit path in the function resets state at the end; this one didn't. Now
   it resets state to `LISTENING` before returning, same as every other exit
   path.

2. **Conversation-history trimming could orphan a `tool` response message.**
   Every tool call appends an `assistant` message with `tool_calls` followed
   immediately by matching `tool` response message(s). The `MAX_HISTORY`
   hard-slice (`self._conversation[-MAX_HISTORY:]`) cut by raw message count
   with no awareness of that pairing, so a long session could end up sending
   the LLM a `tool` message with no preceding `tool_calls` — an invalid
   sequence most chat APIs reject. Now the trim additionally drops any
   leading orphaned `tool` message after the count-based cut.

## core/llm_client.py

3. **Qwen3/reasoning-model "thinking" mode was silently eating the whole
   response.** Ollama's reasoning models (qwen3, deepseek-r1, …) do a hidden
   `<think>...</think>` reasoning pass before the real answer unless you
   pass `"think": false`. Nothing in the client set that flag, so with
   `num_predict` capped at 150 tokens the model could burn its whole budget
   on invisible reasoning and never emit real content or a tool call — the
   UI just sat on "Thinking" with nothing coming back. Added `"think": false`
   to every native-Ollama request (`call_llm`, `call_llm_stream`,
   `call_llm_text`, `warmup_model`).

4. **Provider routing was inverted** in `ensure_ollama_running`,
   `warmup_model`, and `call_llm_stream` — each had
   `if provider == "ollama":` guarding the *OpenAI-compatible* server logic,
   with the real native-Ollama code stuck in the `else` branch. Since your
   default config is `llm_provider: "ollama"`, every streaming chat call was
   going through the wrong code path. Flipped the conditions.

5. **Dead/unreachable code removed from `call_llm`.** A full duplicate
   Ollama request block sat after a `return`/`raise`, so it could never
   execute — including its connection-retry-on-failure logic. Moved that
   retry logic into the actual reachable Ollama branch instead of deleting
   it outright.

6. **Streaming generator could end silently with no `done` event.** If
   Ollama's connection closed without ever sending a final `"done": true`
   chunk, the generator just stopped — the caller's `for event in
   call_llm_stream(...)` loop would exit with no result and no error, which
   (combined with bug #1 above) is another path to a stuck UI. Added a
   fallback that always yields a `done` event when the stream ends, even if
   the server never sent one.

## core/smart_context.py

7. **Token-budget compression had the same orphaned-`tool`-message bug as
   #2**, but here it runs on *every single turn*
   (`compress_if_needed(messages, token_budget=3000)`), so it's the more
   likely of the two to actually trigger. Now drops an `assistant`
   `tool_calls` message together with its paired `tool` response(s) as one
   unit, and guards against a leftover orphan either way.

## agent/executor.py

8. **Redundant `import re` inside a loop body** in `_inject_context`
   (imported fresh on every iteration of every parameter, every step).
   Harmless today since it's the same module, but exactly the pattern that
   silently breaks if a local variable is ever named `re`. Moved to use the
   existing top-level import.

---

All four files pass `python -m py_compile` cleanly. The two orphaned-tool-
message fixes were also verified with a synthetic conversation that forces
a trim mid tool-call pair (no orphaned message in the output).
