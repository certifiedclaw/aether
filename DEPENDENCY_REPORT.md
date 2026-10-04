# Aether — Dependency Report

> **Auto-generated.** Rerun `python -m tools.maintenance.dep_watcher` (or `run_all`) to refresh. Requires internet access.

Generated: 2026-09-20T01:09:39.575899+00:00

Note: `requirements.txt` doesn't pin exact versions, so this can't flag "you're 3 versions behind" — it reports the latest available so you can decide whether to pin and bump.

## Core infra (not in requirements.txt)

| Component | Latest | Why it matters |
|---|---|---|
| [Ollama](https://github.com/ollama/ollama) | v0.34.2 | local LLM runtime — core/llm_client.py talks to it |
| [faster-whisper (engine)](https://github.com/SYSTRAN/faster-whisper) | v1.2.1 | default STT engine per requirements.txt |
| [Kokoro TTS](https://github.com/hexgrad/Kokoro-82M) | unknown (lookup failed) | offline TTS option, alternative to edge-tts |

## Python packages (from requirements.txt)

| Package | Latest on PyPI | Notes |
|---|---|---|
| fastapi | 0.141.1 |  |
| uvicorn | 0.53.0 |  |
| cryptography | 50.0.1 |  |
| python-multipart | 0.0.32 |  |
| PyQt6 | 6.11.0 |  |
| psutil | 7.2.2 |  |
| pillow | 12.3.0 |  |
| sounddevice | 0.5.6 |  |
| numpy | 2.5.3 |  |
| soundfile | 0.14.0 |  |
| requests | 2.34.2 |  |
| playwright | 1.63.0 |  |
| beautifulsoup4 | 4.15.0 |  |
| duckduckgo-search | 8.1.1 |  |
| pyautogui | 0.9.54 |  |
| pyperclip | 1.11.0 |  |
| pygetwindow | 0.0.9 |  |
| send2trash | 2.1.0 |  |
| mss | 10.2.0 |  |
| opencv-python | 5.0.0.93 |  |
| pywinauto | 0.6.9 |  |
| comtypes | 1.4.16 |  |
| pycaw | 20251023 |  |
| win10toast | 0.9 |  |
| youtube-transcript-api | 1.2.4 |  |
| python-pptx | 1.0.2 |  |
| miniaudio | 1.71 |  |
| faster-whisper | 1.2.1 |  |
| edge-tts | 7.2.8 |  |
| sentence-transformers | 6.1.0 |  |
| PyMuPDF | 1.28.2 |  |
| python-whois | 0.9.6 |  |
| dnspython | 2.8.0 |  |
| watchdog | 6.0.0 |  |
| keyboard | 0.13.5 |  |
| python-docx | 1.2.0 |  |
| langchain | 1.4.2 | Fast-moving API; pin a version once Aether's LangChain tool wiring is stable. |
| langchain-community | 0.4.2 |  |
| langchain-ollama | 1.1.0 |  |
| open-interpreter | 0.4.3 | Depends on litellm; historically conflicts with langchain/llama-index pins. Consider a separate venv. |
| llama-index | 0.14.24 | Splits into many llama-index-* subpackages frequently; check changelog before bumping. |
| llama-index-llms-ollama | 0.11.0 |  |
| llama-index-embeddings-ollama | 0.10.0 |  |
| qrcode | 8.2 |  |
| wikipedia-api | 0.16.0 |  |
| google-generativeai | 0.8.6 |  |
| google-genai | 2.24.0 |  |

## Flagged for extra attention
- **langchain** — Fast-moving API; pin a version once Aether's LangChain tool wiring is stable.
- **open-interpreter** — Depends on litellm; historically conflicts with langchain/llama-index pins. Consider a separate venv.
- **llama-index** — Splits into many llama-index-* subpackages frequently; check changelog before bumping.
