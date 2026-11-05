"""
Aether — Agent Executor
Replaces google.generativeai with local Ollama via core.llm_client.
"""
import json
import re
import sys
import threading
import subprocess
import tempfile
import os
from pathlib import Path
from typing import Callable

from agent.planner       import create_plan, replan
from agent.error_handler import analyze_error, generate_fix, ErrorDecision
from core.llm_client     import call_llm_text
from core                import sandbox
import logging

logger = logging.getLogger("aether")


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
_MAX_GENERATED_CODE = 4000


class GeneratedCodeNotRun(RuntimeError):
    """Generated Python was blocked or declined and must not be retried silently."""


def _sandbox_policy() -> "sandbox.SandboxPolicy":
    try:
        from config import get_config
        return sandbox.SandboxPolicy.from_config(get_config())
    except Exception:
        return sandbox.SandboxPolicy()      # fail closed: strictest defaults


# ---------------------------------------------------------------------------
# Code generation helper (replaces _run_generated_code with Gemini)
# ---------------------------------------------------------------------------

def _run_generated_code(description: str, speak: Callable | None = None) -> str:
    if speak:
        speak("Writing custom code for this task, sir.")

    home      = Path.home()
    desktop   = home / "Desktop"
    downloads = home / "Downloads"
    documents = home / "Documents"

    if not desktop.exists():
        try:
            import winreg
            key     = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
            )
            desktop = Path(winreg.QueryValueEx(key, "Desktop")[0])
        except Exception:
            pass

    policy = _sandbox_policy()
    if not policy.enabled or policy.allow_subprocess:
        pkg_rule = "Install missing packages with subprocess + pip if needed. "
    else:
        pkg_rule = ("Do not install packages or start other programs: they are blocked. "
                    "If a needed package is missing, print which one and stop. ")
    if not policy.enabled or policy.allow_network:
        net_rule = ""
    else:
        net_rule = "The network is blocked; do not make web requests. "

    if policy.enabled and policy.backend == "docker":
        shared = "".join(f"  {inside} = {host} ({mode})\n"
                         for host, inside, mode in policy.mount_map())
        paths_block = ("ENVIRONMENT: a Linux container. Working directory is /work (temporary, "
                       "deleted afterwards). Your PC's files are NOT visible except these "
                       "shared folders:\n" + (shared or "  (none)\n"))
    else:
        paths_block = (f"SYSTEM PATHS:\n"
                       f"  Desktop   = r'{desktop}'\n"
                       f"  Downloads = r'{downloads}'\n"
                       f"  Documents = r'{documents}'\n"
                       f"  Home      = r'{home}'\n")

    system = (
        "You are an expert Python developer. "
        "Write clean, complete, working Python code. "
        "Use standard library + common packages. "
        f"{pkg_rule}{net_rule}"
        "Return ONLY the Python code. No explanation, no markdown, no backticks.\n\n"
        f"{paths_block}"
    )
    prompt = f"Write Python code to accomplish this task:\n\n{description}"

    try:
        code = call_llm_text(prompt, system=system, tier="deep")
        code = re.sub(r"```(?:python)?", "", code).strip().rstrip("`").strip()

        if len(code) > _MAX_GENERATED_CODE:
            raise GeneratedCodeNotRun(
                f"Generated code was not run because it exceeds the {_MAX_GENERATED_CODE}-character review limit.")

        from core.safety import confirm_action
        if not confirm_action(
                "run model-generated Python code",
                f"Task: {description}\n\n{policy.describe()}\n\n"
                f"Complete generated code:\n{code}"):
            raise GeneratedCodeNotRun("Cancelled: you did not approve running the generated code.")

        logger.info("[Executor] Running generated code in the sandbox "
                    f"(enabled={policy.enabled}, network={policy.allow_network}, "
                    f"subprocess={policy.allow_subprocess})")

        result = sandbox.run_python(code, policy)
        if result.timed_out:
            raise RuntimeError(f"Generated code timed out after {policy.timeout_s} seconds.")

        output = result.stdout.strip()
        error  = result.stderr.strip()

        if result.returncode == 0 and output:
            return output
        elif result.returncode == 0:
            # Exit 0 with no output proves the script ran, not that it did the task.
            return ("Code ran with exit code 0 but printed nothing; "
                    "the result is unverified.")
        elif error:
            raise RuntimeError(f"Code error: {error[:400]}")
        raise RuntimeError(
            f"Code exited with status {result.returncode} and no error output.")

    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Generated code failed: {e}")


# ---------------------------------------------------------------------------
# Context injection
# ---------------------------------------------------------------------------

def _detect_language(text: str) -> str:
    try:
        return call_llm_text(
            f"What language is this text written in? "
            f"Reply with ONLY the language name in English (e.g. Turkish, English, French).\n\n"
            f"Text: {text[:200]}",
            tier="fast",
        ).strip()
    except Exception:
        return "English"


def _translate_to_goal_language(content: str, goal: str) -> str:
    if not goal:
        return content
    try:
        target_lang = _detect_language(goal)
        logger.info(f"[Executor] 🌐 Translating to: {target_lang}")
        prompt = (
            f"You are a professional translator. "
            f"Translate the following text into {target_lang}.\n"
            f"IMPORTANT:\n"
            f"- Translate EVERYTHING, leave nothing in English\n"
            f"- Keep all facts, numbers, and data intact\n"
            f"- Keep the structure and formatting\n"
            f"- Output ONLY the translated text, nothing else\n\n"
            f"Text to translate:\n{content[:4000]}"
        )
        translated = call_llm_text(prompt)
        logger.info(f"[Executor] ✅ Translation done ({target_lang})")
        return translated
    except Exception as e:
        logger.warning(f"[Executor] ⚠️ Translation failed: {e}")
        return content


def _inject_context(params: dict, tool: str, step_results: dict, goal: str = "") -> dict:
    """
    Inject results from previous steps into the current step's parameters.

    Two injection modes:
    1. Explicit placeholder  — any parameter value that is exactly "{step_N}"
       (e.g. "{step_1}") is replaced with that step's result.
    2. Auto-inject content  — for parameters named 'content', 'text', 'input',
       or 'query' that are empty or very short, the last substantive step result
       is injected (after translation if the goal language differs from English).
    """
    if not step_results:
        return params

    params = dict(params)

    # Collect step results in order, filtering out trivial ones
    ordered_results = [
        v for k, v in sorted(step_results.items())
        if v and len(str(v)) > 50 and v not in ("Done.", "Completed.", "Task cancelled.")
    ]
    last_result = ordered_results[-1] if ordered_results else None

    # ── Mode 1: explicit {step_N} placeholders ─────────────────────────────
    for key, val in list(params.items()):
        if not isinstance(val, str):
            continue
        for match in re.findall(r"\{step_(\d+)\}", val):
            step_key = int(match)
            replacement = step_results.get(step_key) or step_results.get(str(step_key), "")
            if replacement:
                params[key] = val.replace(f"{{step_{match}}}", str(replacement))

    # ── Mode 2: auto-inject into content-bearing parameters ────────────────
    _CONTENT_PARAMS = {"content", "text", "input", "body", "message_text"}
    for pname in _CONTENT_PARAMS:
        if pname not in params:
            continue
        val = params[pname]
        if val and len(str(val)) >= 50:
            continue   # already has real content
        if not last_result:
            continue

        combined   = "\n\n---\n\n".join(ordered_results)
        translated = _translate_to_goal_language(combined, goal)
        params[pname] = translated
        logger.info(f"[Executor] 💉 Auto-injected + translated into '{pname}'")
        break   # only inject once

    return params


# ---------------------------------------------------------------------------
# Tool routing
# ---------------------------------------------------------------------------

def _call_tool(tool: str, parameters: dict, speak: Callable | None) -> str:
    if tool == "open_app":
        from actions.open_app import open_app
        return open_app(parameters=parameters, player=None) or "Done."

    elif tool == "web_search":
        from actions.web_search import web_search
        return web_search(parameters=parameters, player=None) or "Done."

    elif tool == "game_updater":
        from actions.game_updater import game_updater
        return game_updater(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool == "browser_control":
        from actions.browser_control import browser_control
        return browser_control(parameters=parameters, player=None) or "Done."

    elif tool == "file_controller":
        from actions.file_controller import file_controller
        return file_controller(parameters=parameters, player=None) or "Done."

    elif tool == "code_helper":
        from actions.code_helper import code_helper
        return code_helper(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool == "dev_agent":
        from actions.dev_agent import dev_agent
        return dev_agent(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool == "screen_process":
        from actions.screen_processor import screen_process
        result = screen_process(parameters=parameters, player=None)
        return result if isinstance(result, str) else "Screen captured and analyzed."

    elif tool == "send_message":
        from actions.send_message import send_message
        return send_message(parameters=parameters, player=None) or "Done."

    elif tool == "reminder":
        from actions.reminder import reminder
        return reminder(parameters=parameters, player=None) or "Done."

    elif tool == "youtube_video":
        from actions.youtube_video import youtube_video
        return youtube_video(parameters=parameters, player=None) or "Done."

    elif tool == "weather_report":
        from actions.weather_report import weather_action
        return weather_action(parameters=parameters, player=None) or "Done."

    elif tool == "computer_settings":
        from actions.computer_settings import computer_settings
        return computer_settings(parameters=parameters, player=None) or "Done."

    elif tool == "desktop_control":
        from actions.desktop import desktop_control
        return desktop_control(parameters=parameters, player=None) or "Done."

    elif tool == "computer_control":
        from actions.computer_control import computer_control
        return computer_control(parameters=parameters, player=None) or "Done."

    elif tool == "generated_code":
        description = parameters.get("description", "")
        if not description:
            raise ValueError("generated_code requires a 'description' parameter.")
        return _run_generated_code(description, speak=speak)

    elif tool == "flight_finder":
        from actions.flight_finder import flight_finder
        return flight_finder(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool == "extra_tool":
        from actions.extra_tools import run_extra_tool
        return run_extra_tool(parameters.get("action", ""), parameters)

    else:
        # Fail closed: an unrecognised tool name (planner typo, hallucinated tool) must
        # surface as an error the error handler can replan around. It used to be turned
        # into model-written code, which turned any bad name into arbitrary execution.
        logger.warning(f"[Executor] Unknown tool '{tool}' -- refusing to guess")
        raise ValueError(f"Unknown tool '{tool}'.")


# ---------------------------------------------------------------------------
# AgentExecutor
# ---------------------------------------------------------------------------

class ExecutionOutcome(str):
    """A result string that also says how the run ended.

    Subclasses str so every existing caller (voice, tests, speak) keeps working;
    TaskQueue reads `.status` ("succeeded" | "failed" | "cancelled") instead of
    assuming that any returned string means success. Plain str == succeeded."""

    def __new__(cls, text: str, status: str = "succeeded"):
        obj = super().__new__(cls, text)
        obj.status = status
        return obj


def outcome_status(result) -> str:
    return getattr(result, "status", "succeeded")


class AgentExecutor:

    MAX_REPLAN_ATTEMPTS = 2

    def execute(
        self,
        goal:        str,
        speak:       Callable | None        = None,
        cancel_flag: threading.Event | None = None,
    ) -> str:
        logger.info(f"\n[Executor] 🎯 Goal: {goal}")

        replan_attempts = 0
        completed_steps: list = []
        step_results:    dict = {}
        plan = create_plan(goal)

        while True:
            steps = plan.get("steps", [])
            if not steps:
                msg = "I couldn't create a valid plan for this task, sir."
                if speak: speak(msg)
                return ExecutionOutcome(msg, "failed")

            # Chained approvals (#67): one dialog for the whole plan instead of one per step.
            try:
                from core import risk as _risk
                if _risk.chained_approvals_enabled():
                    _ok, _n = _risk.request_plan_approval(goal, steps)
                    if not _ok:
                        msg = "Cancelled: the plan was not approved, so nothing was done."
                        if speak: speak(msg)
                        return ExecutionOutcome(msg, "cancelled")
            except Exception as _e:  # noqa: BLE001 - fall back to per-step approvals
                logger.warning(f"[Executor] plan approval unavailable, asking per step: {_e}")

            success      = True
            failed_step  = None
            failed_error = ""

            for step in steps:
                if cancel_flag and cancel_flag.is_set():
                    if speak: speak("Task cancelled, sir.")
                    return ExecutionOutcome("Task cancelled.", "cancelled")

                step_num = step.get("step", "?")
                tool     = step.get("tool", "generated_code")
                desc     = step.get("description", "")
                params   = step.get("parameters", {})
                params   = _inject_context(params, tool, step_results, goal=goal)

                logger.info(f"\n[Executor] ▶️ Step {step_num}: [{tool}] {desc}")

                attempt = 1
                step_ok = False

                while attempt <= 3:
                    if cancel_flag and cancel_flag.is_set():
                        break
                    try:
                        result = _call_tool(tool, params, speak)
                        step_results[step_num] = result
                        completed_steps.append(step)
                        logger.info(f"[Executor] ✅ Step {step_num} done: {str(result)[:100]}")
                        step_ok = True
                        break

                    except GeneratedCodeNotRun as e:
                        if speak: speak(str(e))
                        return str(e)

                    except Exception as e:
                        error_msg = str(e)
                        logger.warning(f"[Executor] ❌ Step {step_num} attempt {attempt} failed: {error_msg}")

                        recovery = analyze_error(step, error_msg, attempt=attempt)
                        decision = recovery["decision"]
                        user_msg = recovery.get("user_message", "")

                        if speak and user_msg:
                            speak(user_msg)

                        if decision == ErrorDecision.RETRY:
                            attempt += 1
                            import time; time.sleep(2)
                            continue

                        elif decision == ErrorDecision.SKIP:
                            logger.info(f"[Executor] ⏭️ Skipping step {step_num}")
                            completed_steps.append(step)
                            step_ok = True
                            break

                        elif decision == ErrorDecision.ABORT:
                            msg = f"Task aborted, sir. {recovery.get('reason', '')}"
                            if speak: speak(msg)
                            return msg

                        else:  # REPLAN
                            fix_suggestion = recovery.get("fix_suggestion", "")
                            if fix_suggestion and tool != "generated_code":
                                try:
                                    fixed_step = generate_fix(step, error_msg, fix_suggestion)
                                    if speak: speak("Trying an alternative approach, sir.")
                                    res = _call_tool(
                                        fixed_step["tool"],
                                        fixed_step["parameters"],
                                        speak,
                                    )
                                    step_results[step_num] = res
                                    completed_steps.append(step)
                                    step_ok = True
                                    break
                                except Exception as fix_err:
                                    logger.warning(f"[Executor] ⚠️ Fix failed: {fix_err}")

                            failed_step  = step
                            failed_error = error_msg
                            success      = False
                            break

                if cancel_flag and cancel_flag.is_set():
                    if speak: speak("Task cancelled, sir.")
                    return ExecutionOutcome("Task cancelled.", "cancelled")

                if not step_ok and not failed_step:
                    failed_step  = step
                    failed_error = "Max retries exceeded"
                    success      = False

                if not success:
                    break

            if success:
                return self._summarize(goal, completed_steps, speak)

            if replan_attempts >= self.MAX_REPLAN_ATTEMPTS:
                msg = f"Task failed after {replan_attempts} replan attempts, sir."
                if speak: speak(msg)
                return ExecutionOutcome(msg, "failed")

            if speak: speak("Adjusting my approach, sir.")
            replan_attempts += 1
            plan = replan(goal, completed_steps, failed_step, failed_error)

    def _summarize(self, goal: str, completed_steps: list, speak: Callable | None) -> str:
        fallback  = f"All done, sir. Completed {len(completed_steps)} steps for: {goal[:60]}."
        steps_str = "\n".join(f"- {s.get('description', '')}" for s in completed_steps)
        prompt    = (
            f'User goal: "{goal}"\n'
            f"Completed steps:\n{steps_str}\n\n"
            "Write a single natural sentence summarising what was accomplished. "
            "Address the user as 'sir'. Be direct and positive."
        )
        try:
            summary = call_llm_text(prompt, tier="deep")
            if summary:
                if speak: speak(summary)
                return summary
        except Exception:
            pass
        if speak: speak(fallback)
        return fallback
