"""Record the v12 session in the handoff log through the project's own handoff code.

Idempotent: if an entry starting with the same marker already exists, nothing is added.

    python tools\\apply_handoff_v12.py          (from the repo root)

The wording below says what was and was NOT verified. Edit nothing by hand afterwards:
if you want a correction, add a new entry with `python -m tools.maintenance.handoff add`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.maintenance import handoff  # noqa: E402

MARKER = "v12 session (claude.ai chat):"

SUMMARY = (
    f"{MARKER} security hardening + Gemini resilience, on top of the Oct 7 fixes18 state. "
    "(1) Roadmap #23: actions/desktop.py exec sandbox rebuilt: AST allowlist (no imports, no _-prefixed "
    "names/attrs, attribute allowlist), the exact generated code goes to the user via core.safety "
    "(fail-closed), function-only proxies for time/shutil/os_path/pyautogui (getattr/hasattr/ctypes/winreg "
    "removed), and approved code now runs in a child process (actions/desktop_runner.py) that re-validates it "
    "and is killed after desktop_task_timeout (default 30s). "
    "(2) core/risk.py: one declarative tier table (safe/monitor/ask/block, unknown=ask, overrides tighten-only) "
    "enforced by @gated on each tool's entry function so the dispatcher AND the agent executor are covered. "
    "Newly gated: send_message, extra_tool run_python/kill_process/batch_rename, file_processor run, "
    "memory_tool import/prune, code_helper run/build, dev_agent (after planning, shows packages+command), "
    "computer_settings shutdown/restart (UI approval, not the model-fillable 'confirmed' param). "
    "Coverage tests fail if a tool or implemented action is unclassified or an ask-tier tool is unenforced. "
    "(3) llm_client: one retry on Gemini 500/502/503/504 and a thread-safe per-model cooldown "
    "(gemini_retry_delay, gemini_cooldown); invalid-key 400 rests 1h; primary-provider Gemini never blocked. "
    "(4) verify_local.py flags packages declared in requirements.txt but not installed (tools/req_check.py). "
    "(5) .gitignore + config/api_keys.example.json. "
    "VERIFIED: full pytest suite green in a Linux sandbox (477 passed, 2 skipped, with the v12b file set) on a pristine copy of the "
    "Oct 7 rezip; every new gate has a test that fails against the old code. "
    "NOT VERIFIED: anything on Windows or with real Ollama/Gemini (replay 24/24 and the live cooldown/retry "
    "behaviour need your verify run); the confirmation dialogs in the real PyQt UI. "
    "Behaviour changes: send_message, run_python, kill_process, batch_rename, file_processor run, memory prune/"
    "import and shutdown/restart now show a confirmation dialog; headless/verify runs deny them. "
    "computer_control/browser_control are only logged (MONITOR), not asked."
)

FILES = (
    "core/risk.py, core/safety.py, core/llm_client.py, core/tool_dispatch.py, actions/desktop.py, "
    "actions/desktop_runner.py, actions/computer_settings.py, actions/code_helper.py, actions/dev_agent.py, "
    "actions/send_message.py, actions/extra_tools.py, actions/file_processor.py, actions/file_controller.py, "
    "actions/computer_control.py, actions/browser_control.py, actions/game_updater.py, actions/open_app.py, "
    "actions/screen_processor.py, tools/verify_local.py, tools/req_check.py, tools/apply_roadmap_v12.py, "
    "tools/apply_handoff_v12.py, config/config.example.json, config/api_keys.example.json, .gitignore, "
    "tests/ (test_risk, test_desktop_sandbox, test_desktop_timeout, test_execution_gates, "
    "test_dangerous_confirmation, test_req_check, test_apply_roadmap, test_apply_handoff, test_invariants, "
    "conftest)"
)

NEXT = (
    "Ian: unzip aether_v12b over the folder; python tools\\\\apply_roadmap_v12.py; python tools\\\\verify_local.py "
    "(expect ~477 tests with v12b, ~502 once v12c is on top; replay 24/24, 'ok' in the Requirements block); then "
    "python -m tools.maintenance.roadmap done 23 and done 30 once verified (#30 is the exec timeout, added by "
    "apply_roadmap_v12). Before the FIRST git commit: git status must show no api_keys.json/.key/memory.db. "
    "Rotate the Gemini key if any full-folder zip was shared. Check old history: git show 816cd23:data/memory.db "
    "(1 row; origin/master has it). Next work: roadmap #25 (approve a whole computer_control task once) and #27/#32 "
    "(owner tool toggles; UI-only way to loosen a tier) so the new dialogs stop feeling like friction."
)


MARKER_TASK = "v12c session (claude.ai chat):"

SUMMARY_TASK = (
    f"{MARKER_TASK} roadmap item 'approve a whole computer_control/browser_control task once'. "
    "New `task` tier in core/risk.py: the first input action in a user request (click/type/press/hotkey/"
    "scroll/drag... in computer_control; click/type/press/fill_form in browser_control) shows ONE dialog that "
    "quotes the user's own words; approval covers that tool for THAT request only (default 60 actions / "
    "120 s; config risk_task_max_actions / risk_task_ttl, clamped 1-500 / 10-900). A refusal sticks for the "
    "rest of the request so a retrying model cannot spam dialogs. conversation_core calls risk.begin_turn() "
    "for each user message (system alerts excluded), which ends every earlier approval. ask-tier actions are "
    "never covered by a grant. Calls are serialised per tool, so concurrent actions give one dialog; a new "
    "request starting while a dialog is open cancels instead of carrying the approval over. Screenshots/reads "
    "stay safe and browser navigation stays monitor. "
    "VERIFIED: full pytest suite green in a Linux sandbox (502 passed, 2 skipped); the hook test fails if the begin_turn call is removed. "
    "NOT VERIFIED: the real PyQt dialog, how often it prompts in daily use, and whether 60 actions / 120 s are "
    "the right limits (note: those two settings live in a model-writable config; they are clamped, so the worst "
    "case is a longer window after the user's first approval, never an approval-free one)."
)

FILES_TASK = (
    "core/risk.py, core/conversation_core.py, tools/apply_handoff_v12.py, tests/conftest.py, "
    "tests/test_risk.py, tests/test_risk_task.py, tests/test_apply_handoff.py"
)

NEXT_TASK = (
    "Ian: verify the earlier v12 entry first, then use computer control for a few days. If the dialog feels "
    "too frequent or too rare tune risk_task_ttl / risk_task_max_actions. Then mark the roadmap item "
    "'Approve a whole computer_control/browser_control task once' done. Next candidates: owner per-tool "
    "toggles and a UI-only way to loosen a tier (so confirmation dialogs can offer 'allow for this session'), "
    "then the non-blocking confirmation banner."
)

MARKER_KEYS = "v12d session (claude.ai chat):"

SUMMARY_KEYS = (
    f"{MARKER_KEYS} central API-key handling + a config-wipe bug. "
    "(1) core/credentials.py: get_secret(name, cfg) resolves env (AETHER_<NAME> then <NAME>) > OS keyring "
    "(optional package, lazy import, 30 s cache, any failure falls back silently, value never logged) > config "
    "file. Wired into the only three sites that consume a secret: llm_client (_auth_headers, "
    "get_gemini_settings) and tts (ElevenLabs). Opt-in: nothing changes until you migrate. "
    "(2) tools/secrets_cli.py: status | set | delete | migrate (dry run by default; --apply stores each key in "
    "the keyring, READS IT BACK and compares before blanking the file entry; atomic file write; other settings "
    "kept). (3) verify_local.py now prints where each key comes from (env/keyring/file), never the value. "
    "(4) FOUND AND FIXED: both ui.py settings-save paths overwrote api_keys.json with only the dialog's 11 "
    "fields, silently deleting every other setting (ntfy_topic, gemini_escalation_model, gemini_cooldown, "
    "risk_overrides, risk_task_*, ...) whenever Save was pressed. core/config_io.save_config() now merges into "
    "the existing file atomically, and keeps keyring-resident keys coherent (typing a new key updates the "
    "keyring because the keyring wins at read time; a blank field never deletes a keyring entry). "
    "CORRECTION: earlier notes said 8+ file readers needed migrating; only 3 sites consume secrets, the rest read "
    "non-secret settings from the same file. "
    "VERIFIED: full pytest suite green in a Linux sandbox (542 passed, 2 skipped), including the real keyring "
    "library with an in-memory backend; the three consumers and ui.py were reverted one at a time and the new "
    "tests failed each time. "
    "NOT VERIFIED: the real Windows Credential Manager backend, keyring on Python 3.14, and the real PyQt "
    "settings dialog saving (ui.py is checked by a source-level test only, PyQt is not available in the sandbox). "
    "Behaviour after you migrate: the key fields in the settings dialog show blank (the key is in the keyring)."
)

FILES_KEYS = (
    "core/credentials.py, core/config_io.py, core/llm_client.py, core/tts.py, ui.py, tools/verify_local.py, "
    "tools/secrets_cli.py, tools/apply_roadmap_v12.py, tools/apply_handoff_v12.py, requirements-optional.txt, "
    "tests/test_credentials.py, tests/test_config_io.py, tests/test_apply_roadmap.py, tests/test_apply_handoff.py"
)

NEXT_KEYS = (
    "Ian: copy config/api_keys.json somewhere safe first (it holds all your settings; keep that copy out of any "
    "zip you share). Then: python -m pip install keyring; python tools\\secrets_cli.py status; "
    "python tools\\secrets_cli.py migrate (dry run); python tools\\secrets_cli.py migrate --apply; run "
    "tools\\verify_local.py and look for 'gemini_api_key  set (keyring)'. After migrating, rotate the Gemini key "
    "if any zip or git history ever contained api_keys.json with the key in it, and delete old zips. Close the "
    "'Central get_secret()' roadmap item once verified on Windows."
)

ENTRIES = [
    (MARKER, SUMMARY, FILES, NEXT),
    (MARKER_TASK, SUMMARY_TASK, FILES_TASK, NEXT_TASK),
    (MARKER_KEYS, SUMMARY_KEYS, FILES_KEYS, NEXT_KEYS),
]


def apply() -> int:
    """Add each entry whose marker is not already in the log. Returns how many were added."""
    added = 0
    for marker, summary, files, nxt in ENTRIES:
        if any(e.get("summary", "").startswith(marker) for e in handoff._load()["entries"]):
            continue
        handoff.cmd_add(argparse.Namespace(by="Claude (claude.ai chat)", summary=summary,
                                           files=files, next=nxt))
        added += 1
    return added


def main() -> int:
    if apply() == 0:
        print("v12 handoff entries already present; nothing added.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
