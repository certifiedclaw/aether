# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [v1.0.0] — Aether

First release under the **Aether** name. This is the same engine that shipped as
Mark XL v7 (memory system, agent planner, OSINT toolkit, plugin system, dashboard,
etc.) — this release is the rebrand + UI overhaul that turns it into a coherent,
presentable v1.0 rather than a new feature drop. If you're upgrading from a
Mark XL checkout, your `config/api_keys.json` and memory DB are untouched and
will keep working as-is.

### Changed — UI redesign
- Replaced the old blue/orange "targeting HUD" look with a new ethereal
  cyan/purple sci-fi theme (deep space black background, `#00f5ff` primary,
  `#9d4edd` secondary).
- Replaced `HudCanvas` (crosshair/reticle style) with `AetherOrb` — a glowing
  sphere with distinct animated states: idle breathing, listening pulse rings,
  thinking orbit dots, speaking audio bars + light waves, and a red→orange
  error flash.
- Bundled the Rajdhani font (OFL-licensed) for headings/display text, with a
  system-sans fallback for body text.
- Rebranded all in-app strings: window title, header, footer, status badges.
  Removed leftover "J.A.R.V.I.S" and "FatihMakes Industries" references.

### Added
- `Ctrl+Alt+A` — show/hide the window from anywhere.
- `Esc` — collapse the HUD down to just the orb (press again to restore full
  layout). Distinct from `F11` fullscreen.
- `docs/screenshots/` gallery + README screenshot section.
- `CHANGELOG.md` (this file) and `TESTING.md` manual test checklist.
- `version.py` — single-source `__version__`/`APP_NAME` constant, wired into
  the window title, header badge, `/help`, and the diagnostics report.
- Orb now shows a distinct **PROCESSING** state (already existed in the
  widget, wasn't being used) while a tool is actually executing, separate
  from **THINKING** during LLM generation — clearer visual feedback for
  "Aether is doing something" vs. "Aether is composing a reply."
- Tool activity-log lines now include a short argument hint, e.g.
  `SYS: ▶ web_search (query=weather in...)` instead of just the tool name.
- `detect_system_theme()` — best-effort OS light/dark detection (Windows
  registry check + Qt 6.5+ `styleHints().colorScheme()`), logged at startup.
  Aether's HUD stays dark by design either way; this is groundwork for the
  "proper theme switching" item on the roadmap, not a light-mode palette yet.

### Fixed — leftover branding sweep
- OS notifications from the `reminder` tool ("J.A.R.V.I.S Reminder" toast /
  banner / AppleScript / Windows Task Scheduler description) → "Aether".
- YouTube tool's URL-input dialog title ("J.A.R.V.I.S") → "Aether".
- Global hotkey default changed from `Ctrl+Alt+J` to `Ctrl+Alt+A` in
  `core/hotkey_manager.py` (this is the OS-level `keyboard`-package hotkey —
  separate from the in-window `QShortcut` added in the UI redesign, which
  was already on `Ctrl+Alt+A` but the two had drifted out of sync).
  Startup log line now reports whatever hotkey is actually bound instead of
  a hardcoded string.
- `/help` chat command's hotkey hint corrected from `Ctrl+Alt+J` to
  `Ctrl+Alt+A` and now includes `Esc collapse`.
- `core/diagnostics.py` status report header dropped a stale "v3" label in
  favor of the real `version.py` version string.
- No remaining `J.A.R.V.I.S` / `JARVIS` strings anywhere in `.py` files
  (verified with a repo-wide grep). "Mark-XLVIII" mentions in code comments
  (`main.py`, `dashboard/server.py`, `requirements.txt`,
  `config.example.json`) were left as-is — those are accurate historical
  attribution for a *different*, separate predecessor project the dashboard
  code was ported from, not leftover Aether branding.

### Fixed
- N/A this release — purely cosmetic/rebrand changes. See `archive/IMPROVEMENTS_V6.md`
  and earlier for the bug-fix history from the Mark XL line.

### Known gaps / not yet done
- Left/right panel proportions still follow the original layout (compact
  system-monitor sidebar + wider conversation/controls panel) rather than a
  strict 40% conversation / 25% controls split — functional but not a full
  structural reflow.
- Settings/Configure panel still uses its original single-modal layout rather
  than the tabbed (Core / Appearance / Behavior / Shortcuts) design from the
  original spec.
- No wake-word support yet ("Hey Aether") — see roadmap.
- Orb is still QPainter/QWidget-based, not QGraphicsView + cached pixmaps —
  fine on modern hardware in testing, but flagged as a perf risk on
  lower-end machines per the roadmap; not yet profiled.
- No fade in/out on window show/hide, no config validation/migration, no
  "reset to defaults," no window position/size persistence yet.
- Screenshots in the gallery are headless placeholder renders (no live
  model/system data) — replace with real captures before publishing.

---

## Pre-rebrand history (Mark XL / JARVIS lineage)

Condensed from `archive/IMPROVEMENTS.md` → `archive/IMPROVEMENTS_V6.md`. Kept here for
reference; those files remain in the repo with full detail.

- **v7 / Mark XL** — Multi-panel UI, agent planning loop, hybrid memory/RAG,
  OSINT toolkit v2.0 (80+ platform username search), STT/TTS pipelines,
  plugin system, chat-history import from Claude/ChatGPT/Gemini exports.
- **v6** — Config adapter dotted-key lookup bug fixed; config-cache
  invalidation on reconfigure so subsystems pick up changes without restart.
- **v4–v5** — Six confirmed architectural bugs fixed (init-order
  `AttributeError`s, etc.); neural network canvas animation; multi-theme
  system; task queue; deep research panel; model manager; settings dialog
  with multiple tabs.
- **JARVIS v1–v4.2** — Original architecture: PySide6/PyQt UI, Ollama backend,
  ChromaDB semantic memory, RAG, tool/agent system, STT/TTS, browser control
  via CDP, plugin system. LangChain / LlamaIndex / Open Interpreter
  integrations explored.

---

## How to tag this release

```bash
git add -A
git commit -m "Aether v1.0.0 — rebrand + ethereal UI redesign"
git tag -a v1.0.0 -m "Aether v1.0.0"
git push origin main --tags
```

If you'd rather ship this as a `v0.5.0` (pre-1.0, since wake word / tabbed
settings / full test pass are still open), just swap the version string above —
nothing in the code depends on the tag itself.
