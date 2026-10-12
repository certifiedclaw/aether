# Aether — Manual Test Checklist (pre-release)

Run through this before tagging a release. Check items off as you go; anything
that fails, note the exact repro steps and file it (or paste the traceback back
to me and I'll fix it).

Environment to note before starting:
- OS: ______  GPU: ______  Ollama version: ______
- `llm_model`: ______  `stt_engine`: ______  `tts_engine`: ______

---

## 1. Voice interaction flow

- [ ] Fresh launch → **Initialisation** overlay appears, no crash on first boot
- [ ] Mic picks up speech within ~1-2s of speaking (no long dead air)
- [ ] STT transcript shown in the activity log matches what you actually said
- [ ] Orb transitions: idle → **listening** (pulse rings) → **thinking**
      (orbit dots) → **speaking** (audio bars) happen in the right order,
      no state stuck/frozen
- [ ] TTS starts speaking on the *first sentence*, not after the full
      response finishes generating (streaming claim from the README)
- [ ] `F4` mutes/unmutes the mic — orb turns to the muted color, log confirms
- [ ] Talk over Aether while it's speaking (barge-in) — confirm what actually
      happens (interrupts cleanly / ignores you / crashes) and note it
- [ ] Long response (>30s of speech) doesn't desync audio from the orb's
      speaking animation
- [ ] Background noise / silence doesn't trigger false wake-ups or empty
      transcripts spamming the log
- [ ] Switch `stt_language` to a non-English locale (or `auto`) and confirm
      detection works if you use another language day-to-day

## 2. Tool calling reliability

Test each tool at least once with a natural voice/text request (not the exact
tool name) and confirm it (a) gets called at all, (b) returns a sensible
result, (c) doesn't hang the UI while running.

- [ ] `open_app` — "open notepad" / "go to youtube.com"
- [ ] `web_search` — "search for today's weather in Atlanta"
- [ ] `weather_report` — "what's the weather like right now"
- [ ] `send_message` — WhatsApp/Telegram message (if configured)
- [ ] `reminder` — "remind me in 2 minutes to check the oven"
- [ ] `youtube_video` — "play [song] on youtube"
- [ ] `screen_process` — "what's on my screen right now"
- [ ] `computer_settings` — "turn the volume down"
- [ ] `browser_control` — a multi-step browser task
- [ ] `file_controller` — "how much disk space do I have left"
- [ ] `desktop_control` — "change my wallpaper"
- [ ] `code_helper` — "write me a python function that reverses a string"
- [ ] `dev_agent` — a small multi-file project request
- [ ] `game_updater` — "update my Steam games" (if Steam installed)
- [ ] `flight_finder` — "find flights from Atlanta to Tokyo next month"
- [ ] `file_processor` — drop a PDF/image/CSV in the file zone, ask about it
- [ ] Ambiguous request that could match 2+ tools — confirm it picks a
      reasonable one instead of looping (loop guard should catch true loops —
      try to force one, e.g. repeatedly asking for something a tool can't do)
- [ ] Tool failure case (e.g. no internet for `web_search`) — confirm graceful
      error message instead of a silent hang or crash

## 3. Memory system

- [ ] Tell Aether a personal fact ("I have a dog named Max"), start a **new**
      session, ask a related question — confirm it recalls the fact
- [ ] `/memstats` returns sensible numbers (not zeroed out / erroring)
- [ ] `/backup` produces a zip file you can actually find on disk
- [ ] `/prune` runs without deleting things you still needed (spot check
      before/after)
- [ ] Set an active goal, restart the app, confirm the goal persisted
      (goal manager survives restart per README)
- [ ] RAG: point it at a small test folder (Markdown/PDF), ask a question only
      answerable from that content, confirm it actually retrieves from it
      and doesn't hallucinate an answer instead
- [ ] Memory recall stays accurate after ~20-30 turns in one session (no
      drift/confusion between facts)

## 4. Different Ollama models

Run the same 5-10 minute smoke test (a couple of chat turns + one tool call +
one memory recall) against each model you actually plan to support:

- [ ] `qwen3:8b` (your current default)
- [ ] At least one smaller model (e.g. `qwen2.5:3b` or similar) — confirm
      tool-calling still works; smaller models are more likely to malform
      tool-call JSON
- [ ] At least one larger/different family (e.g. `llama3.2`, `mistral`) —
      confirm prompt.txt formatting/instructions still get followed
- [ ] Switch models via **⚙ Configure** mid-session (no restart) — confirm
      the switch actually takes effect (this was a real bug in the v6 config
      adapter — worth double-checking it stays fixed)
- [ ] Note response latency / tokens-per-sec per model in the Message Stats
      panel for your own reference

---

## After the pass

- [ ] Update `CHANGELOG.md` "Known gaps" section with anything you found and
      decided to ship anyway vs. fix first
- [ ] Replace placeholder screenshots in `docs/screenshots/` with real
      captures from one of these test runs
- [ ] Tag the release (see `CHANGELOG.md` → "How to tag this release")
