# Aether

Aether is a desktop voice and chat assistant built around local inference. It combines a PyQt6 interface, Ollama-compatible language models, speech recognition, speech synthesis, memory, and tools for research and desktop tasks.

Local is the default, not a requirement: optional hosted model providers, web research, Edge TTS, ElevenLabs, and other integrations may send requests to external services. Review the provider and its data policy before enabling it.

## What you can do

- **Talk or type:** Use the microphone for speech input, or send text in the desktop chat.
- **Browse actions:** Use the **Actions** tab for quick tasks, or choose **Browse All Actions** to search enabled capabilities, fill in action details, and launch them directly. Sensitive actions continue to use Aether's approval checks.
- **Research:** Start a multi-step research task from the Research quick action when that integration is enabled. If it is unavailable, Aether offers a single-search fallback.
- **Choose voice and model providers:** Configure Whisper or Vosk; Edge TTS, Kokoro, or ElevenLabs; and a local Ollama/LM Studio server or supported hosted providers.
- **Use local memory:** Aether can retain facts and chat history locally between turns. It also includes memory backup, export/import, and maintenance commands.
- **Connect optional integrations:** Use document retrieval, LangChain research, LlamaIndex, MCP servers, plugins, and the web dashboard when configured.
- **Run without a desktop:** Headless mode is available for server use and scripted text testing.

The app is cross-platform, but available actions, audio devices, and optional dependencies vary by operating system and configuration.

## Requirements

- Python 3.10 or newer. Python 3.11 or 3.12 is recommended by the installer scripts.
- [Ollama](https://ollama.com) for the default local language-model backend, and at least one model pulled locally.
- A microphone and speakers/headphones for voice interaction.
- Internet access only for features that use external services or download dependencies/models.

For example, install Ollama, then download a model:

```powershell
ollama pull qwen2.5:7b
```

## Install and launch

Clone or download this repository and open a terminal in the project folder.

### Windows

Run `install.bat`, then `run.bat`. The installer creates a `venv` and installs dependencies. It asks whether `run.bat` should start the desktop app or headless mode; you can change this later in `aether_mode.txt`.

### Linux or macOS

Use Python 3.11 or 3.12, then run:

```bash
chmod +x install.sh run.sh
./install.sh
./run.sh
```

### Manual launch

You can also create and activate a virtual environment yourself, install dependencies, and launch the app:

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux/macOS:
# source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

At first launch, Aether opens its setup screen. Choose the speech recognition, model, and speech synthesis options, then initialize. Some optional engines download models or install their packages on first use.

## Configure Aether

Use **Configure** in the desktop UI to change providers and settings without manually editing JSON. Configuration is stored in `config/api_keys.json`; `config/config.example.json` documents the available settings. Keep real credentials out of source control and shared archives.

API keys can be supplied through environment variables, the operating system keyring, or the local configuration file. The environment/keyring options are preferable for secrets. The optional helper supports checking secret sources, storing keys in the keyring, and migrating keys:

```powershell
python tools\secrets_cli.py status
python -m pip install keyring
python tools\secrets_cli.py set elevenlabs_api_key
```

ElevenLabs requires an API key and a valid voice ID. Edge TTS is the default online voice option; Kokoro runs locally after its model is installed.

### Provider and privacy notes

- **Ollama:** Local inference by default. The model must already be installed with `ollama pull <model>`.
- **LM Studio:** Use its local OpenAI-compatible server URL and select the matching provider in Configure.
- **Gemini / OpenAI API:** Optional hosted inference. Aether has cloud redaction controls; review `cloud_allow`, `cloud_redact`, and fallback settings in `config/config.example.json`.
- **Web and research tools:** Search requests go to the configured search/research providers.
- **Speech synthesis:** Edge TTS and ElevenLabs contact their respective services. Kokoro is the offline option.
- **Plugins and desktop actions:** Plugins execute as trusted Python code. Only enable plugins you have reviewed. Some desktop and file actions can make changes to your computer; approval gates apply to sensitive operations.

## Desktop controls

The right side of the main window separates **Activity** from **Actions**. Quick actions provide common starting points; **Browse All Actions** searches the currently available tools. Availability depends on which integrations and dependencies are enabled.

| Shortcut | Action |
| --- | --- |
| `F4` | Mute or unmute the microphone |
| `F11` | Toggle fullscreen |
| `Ctrl+Alt+A` | Show or hide the window |
| `Esc` | Collapse the window to the orb or restore it |
| `Ctrl+Shift+N` | Start a new chat |

## Chat commands

Enter these in the chat input:

| Command | Purpose |
| --- | --- |
| `/help` | List chat commands |
| `/status` | Run system diagnostics |
| `/doctor` | Check installation, configuration, and runtime health |
| `/doctor fix` | Apply safe fixes found by the doctor |
| `/selftest` | Run subsystem self-tests |
| `/audit` | View recent gated actions and approvals |
| `/audit verify` | Verify the audit log chain |
| `/perf [hours]` | Show model timing, speed, memory, and usage statistics |
| `/resources` | Show resource-governor status |
| `/crashes` | Show recent crash reports |
| `/backup` | Back up memory |
| `/memstats` | Show memory statistics |
| `/prune` | Remove duplicate or old memory records |
| `/export [path]` | Export memory |
| `/import <path>` | Import a memory export |
| `/clear` | Clear the current conversation history |

## Optional web dashboard and headless mode

The FastAPI dashboard is optional and requires its listed server dependencies. The desktop app uses loopback by default. To use the dashboard from another device, deliberately configure a reachable interface and protect access; do not expose it directly to the public internet. See [`docs/SERVER.md`](docs/SERVER.md) for HTTPS, remote access, authentication, the OpenAI-compatible text API, and server deployment.

For a server without a desktop display, set `AETHER_HEADLESS=1`. Headless mode skips the Qt interface and microphone/audio by default; the dashboard provides remote interaction. Set `AETHER_HEADLESS_VOICE=1` only when the server has configured audio devices. For scripted text replay, see `tests/live_test_script.txt`.

## Evaluation and tests

The fixed-prompt evaluation harness can replay the smoke suite, core suite, or both against an installed Ollama model. It temporarily isolates replay memory and writes results under `logs/eval/`.

```powershell
python -m tools.eval_harness run --label qwen --suite all --model qwen2.5:7b
python -m tools.eval_harness list
python -m tools.eval_harness show qwen
```

Run the automated test suite with:

```powershell
python -m pytest tests -q
```

The evaluation harness tests do not require a running model; an actual evaluation run does.

## Project documentation

- [`HANDOFF.md`](HANDOFF.md) — recent work and handoff notes
- [`MAINTENANCE.md`](MAINTENANCE.md) — index to generated project reports
- [`ROADMAP.md`](ROADMAP.md) — active backlog
- [`docs/SERVER.md`](docs/SERVER.md) — headless, dashboard, and API server details
- [`docs/PLUGIN_SECURITY.md`](docs/PLUGIN_SECURITY.md) — plugin trust model
- [`docs/TESTING.md`](docs/TESTING.md) — manual pre-release test checklist

Refresh generated maintenance reports with:

```powershell
python -m tools.maintenance.run_all --skip-deps
```

## License

MIT.
