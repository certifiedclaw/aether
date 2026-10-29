# Aether — Dependency Report

> **Auto-generated.** Rerun `python -m tools.maintenance.dep_watcher` (or `run_all`) to refresh. Requires internet access.

Generated: 2026-10-29T18:32:49.114962+00:00

Note: `requirements.txt` doesn't pin exact versions, so this can't flag "you're 3 versions behind" — it reports the latest available so you can decide whether to pin and bump.

## Core infra (not in requirements.txt)

| Component | Latest | Source | Why it matters |
|---|---|---|---|
| [Ollama](https://github.com/ollama/ollama) | unknown (lookup failed: API releases: HTTP 403 (rate-limited or blocked); web: HTTP 403 (rate-limited or blocked); API tags: HTTP 403 (rate-limited or blocked)) | — | local LLM runtime — core/llm_client.py talks to it |
| [faster-whisper (engine)](https://github.com/SYSTRAN/faster-whisper) | 1.2.1 | PyPI faster-whisper | default STT engine per requirements.txt |
| [Kokoro TTS](https://github.com/hexgrad/kokoro) | 0.9.4 | PyPI kokoro | offline TTS option, alternative to edge-tts |

## Python packages (from requirements.txt)

| Package | Latest on PyPI | Notes |
|---|---|---|
| fastapi | 0.162.0 |  |
| uvicorn | 0.54.0 |  |
| cryptography | 50.0.2 |  |
| python-multipart | 0.0.32 |  |
| PyQt6 | 6.12.0 |  |
| psutil | 7.2.2 |  |
| pillow | 13.0.0 |  |
| sounddevice | 0.5.6 |  |
| numpy | 2.5.4 |  |
| soundfile | 0.14.0 |  |
| requests | 2.34.2 |  |
| playwright | 1.64.1 |  |
| beautifulsoup4 | 4.15.0 |  |
| duckduckgo-search | 8.1.1 |  |
| pyautogui | 0.9.54 |  |
| pyperclip | 1.11.0 |  |
| pygetwindow | 0.0.9 |  |
| send2trash | 2.1.0 |  |
| mss | 10.2.0 |  |
| opencv-python | 5.0.0.93 |  |
| pywinauto | 0.6.9 |  |
| comtypes | 1.4.17 |  |
| pycaw | 20260927 |  |
| win10toast | 0.9 |  |
| youtube-transcript-api | 1.2.4 |  |
| python-pptx | 1.0.2 |  |
| miniaudio | 1.71 |  |
| faster-whisper | 1.2.1 |  |
| edge-tts | 7.3.1 |  |
| sentence-transformers | 6.2.0 |  |
| PyMuPDF | 2.0 |  |
| python-whois | 0.9.6 |  |
| dnspython | 2.9.1 |  |
| watchdog | 6.0.0 |  |
| keyboard | 0.13.5 |  |
| python-docx | 1.2.0 |  |
| langchain | 1.4.7 | Fast-moving API; pin a version once Aether's LangChain tool wiring is stable. |
| langchain-community | 0.4.2 |  |
| langchain-ollama | 1.1.0 |  |
| llama-index | 0.14.25 | Splits into many llama-index-* subpackages frequently; check changelog before bumping. |
| llama-index-llms-ollama | 0.11.0 |  |
| llama-index-embeddings-ollama | 0.10.0 |  |
| qrcode | 8.2 |  |
| wikipedia-api | 0.16.0 |  |
| google-generativeai | 0.8.6 |  |
| google-genai | 2.34.0 |  |

## Flagged for extra attention
- **langchain** — Fast-moving API; pin a version once Aether's LangChain tool wiring is stable.
- **llama-index** — Splits into many llama-index-* subpackages frequently; check changelog before bumping.
