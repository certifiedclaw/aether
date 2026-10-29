# Aether — System Architecture

> A technical deep-dive into Aether's internal mechanics. Read this before modifying core systems.

## 1. High-Level Data Flow

The main loop runs inside `AetherLocal.run()`:

1. **Input**: Audio is captured via `sounddevice` → buffered by `_VADBuffer` (Voice Activity Detection).
2. **STT**: Speech-to-Text (Whisper/faster-whisper or Vosk) converts the audio buffer to text.
3. **Context Assembly**: `_build_system_prompt()` gathers:
   - Static rules from `core/prompt.txt`
   - Long-term memory (`memory/long_term.json`)
   - Active goals (`core/goal_state.py`)
   - Recent episodic memory (Hybrid Memory BM25/Dense/Rerank)
4. **LLM Round**: `call_llm_stream()` sends the context + conversation history + tool schemas to Ollama (or OpenAI-compatible server).
5. **Tool Execution**: If the LLM returns `tool_calls`, `_execute_tool()` dispatches to the appropriate action (e.g., `browser_control`, `file_controller`).
6. **Evaluation**: The `Evaluator` (optional) scores the response for success/failure.
7. **TTS**: The final text is streamed sentence-by-sentence to the TTS engine (EdgeTTS/Kokoro/ElevenLabs) while the UI orb animates.

---

## 2. Core Components

### `core/llm_client.py`
- **Provider abstraction**: Supports `ollama` (native), `openai` (LM Studio/LocalAI), and `gemini`.
- **Streaming**: Parses Ollama's streaming JSON chunks, splits sentences using regex `(?<=[.!?])\s+`, and yields them one by one so TTS can start *before* the full response is generated.
- **Retry logic**: `ensure_ollama_running()` attempts to launch the `ollama serve` subprocess if the `/api/tags` endpoint is unreachable.
- **KV Cache Priming**: `warmup_model()` sends a dummy request with the static system prompt to pre-fill Ollama's attention cache, reducing first-token latency from ~17s to <1s.

### `memory/hybrid_memory.py` & `memory/rag.py`
- **Three-Stage Retrieval**:
  1. **BM25** (keyword) – fast, catches exact names/commands.
  2. **Dense** (sentence-transformers) – semantic similarity for paraphrased queries.
  3. **Cross-encoder Reranker** – re-scores top candidates for final precision.
- **Incremental BM25**: `store()` calls `add_doc()` instead of full `build()`, preventing O(n²) slowdown as memory grows.
- **RAG Index**: Documents are chunked, embedded, and stored in `data/rag_index/`. The `index_directory()` function now batches embeddings (fixed performance bug).

### `agent/` (Planner + Executor)
- **Planner**: Converts a high-level goal (e.g., "Research AI safety and save to Desktop") into a structured JSON plan of steps. It explicitly guards against using the internal `generated_code` tool in plans (replaces it with `web_search`).
- **Executor**: Executes the plan step-by-step. Handles error recovery via `error_handler.py` – it can `retry`, `skip`, `replan`, or `abort` based on the error type.
- **Task Queue**: Background thread pool for long-running agent tasks, with priority support (HIGH/NORMAL/LOW).

### `actions/` (The Toolbox)
- Over 20 distinct tool modules (`browser_control`, `file_controller`, `osint_tools`, etc.).
- **Security**: `core/safety.py` enforces Path ACLs and requires confirmation for high-risk operations (`shell`, `delete`).
- **OS Abstraction**: Most tools have cross-platform logic (`Windows`, `Darwin`, `Linux`) using `pyautogui`, `subprocess`, and `ctypes`.

### `ui.py` (The HUD)
- **AetherOrb**: A custom QWidget that renders the glowing sphere with states: `LISTENING`, `THINKING`, `SPEAKING`, `PROCESSING`, `ERROR`.
- **Metrics Thread**: Background `_SysMetrics` polls CPU/RAM/GPU/Temp using `psutil` and `ctypes` (subprocess-free on supported platforms).
- **Message Stats Panel**: Displays real-time token counts, generation speed, and context-window usage from the last LLM response.

### `dashboard/server.py` (Web Remote)
- A FastAPI server running on `0.0.0.0:8000`.
- **Security**: AES-256-CBC encryption with a session-key-derived key (client-side CryptoJS).
- **Pairing**: One-time 6-char PIN issued by the desktop UI, consumed via `/auto-login` or the QR code flow.
- **WebSockets**: Pushes live logs and file-transfer events to connected browser/phone clients.

---

## 3. Configuration System

- **File**: `config/api_keys.json`
- **Adapter**: `memory/config_manager._MarkConfigAdapter` provides dotted-key lookups (e.g., `memory.chat_history_limit`) with fallback to hardcoded `_DEFAULTS`.
- **Live Reload**: The `⚙ CONFIGURE` button triggers `_do_reconfigure()`, which invalidates the config cache and restarts TTS/STT/LLM connections without a full app restart.

---

## 4. Critical Code Smells (From HEALTH_REPORT)

- **Monoliths**: `main.py` (2297 lines), `ui.py` (2392 lines), `actions/osint_tools.py` (1180 lines).
- **Giant Functions**: `_process_message_inner()` (328 lines), `_execute_tool()` (294 lines).
- **Stray Prints**: 145 `print()` calls that should be replaced with `logging.debug()` or `core.observability.log()`.
- **TODOs**: One lingering TODO in `tools/maintenance/health_check.py`.

---

## 5. Security Layer (New `security/` Package)

- **`Capability` Enum**: Gates actions like `SHELL`, `COMPUTER_CONTROL`, `FILES_UPLOAD`.
- **`SecurityManager`**: Manages device pairing, session tokens (24h TTL), and PIN issuance.
- **Rate Limiting**: IP-based tracking with a 5-attempt window (currently under review – see TESTING_GUIDE).
- **Audit Log**: All authentication and high-risk actions are written as JSONL to `logs/security.log`.