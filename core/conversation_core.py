"""core/conversation_core.py — Aether Conversation Core

Extracted from main.py's AetherLocal god-object (see PROJECT_STATE.md /
HEALTH_REPORT.md history and the Aether-vs-Mark-LII architecture audit,
"Layer 1: Conversation Core"). This is a *mechanical* extraction, not a
behavioral rewrite: every method below is byte-for-byte the same logic
that used to live inline in main.py's AetherLocal class, just moved into
its own module so the turn-processing pipeline (build context -> call
model -> run tools -> finalize) is no longer interleaved in a 2,700-line
file with UI wiring, STT/TTS, dashboard, and bootstrap code.

`ConversationCoreMixin` is meant to be mixed into `AetherLocal`
(`class AetherLocal(ConversationCoreMixin): ...` in main.py) — it isn't
usable standalone because it still reaches into `self.ui`, `self.speak`,
`self._config`, `self._conversation`, `self._conversation_lock`,
`self._hybrid_mem` / `self._semantic_mem`, `self._loop_guard`,
`self._obs`, `self._goals`, `self._plugins`, and `self._execute_tool`
(all still defined on AetherLocal itself) exactly as it did before the
move.

A few names used here (`OLLAMA_TOOLS`, `_NEEDS_LLM_ROUND`,
`_LANGCHAIN_OK`) are computed in main.py at import time from dynamic
tool-declaration gathering that runs *before* AetherLocal is even
defined. Importing them at module load time here would create a circular
import (main.py -> core.conversation_core -> main.py, while main.py is
still mid-import). They're imported lazily inside the method bodies that
use them instead, which is safe because those methods only ever run
after `main` has finished importing and an AetherLocal instance exists.
Everything else below is a normal top-level import, same as it was in
main.py.

Next extraction candidates (not done in this pass): `_handle_chat_command`
(built-in /commands), `_build_system_prompt` (prompt assembly), and
`_execute_tool` (tool dispatch table) are still in main.py. Splitting
those out is the natural continuation of this refactor but each is a
separate, independently-reviewable change.
"""

import json
import logging
import re
import time
import traceback

from core.smart_context import compress_if_needed, estimate_tool_tokens
from core.tier_router import classify as _classify_tier, Tier as _Tier, wants_gemini_for_tools as _wants_gemini_tools
from core.tool_router import filter_tools as _filter_tools_for_turn
from core.tool_router import filter_tools_hard as _filter_tools_hard

_CLOUD_HISTORY_BUDGET = 8000   # message tokens for Gemini-routed turns (schema is extra)


def _local_total_budget() -> int:
    """Total request budget (messages + tool schema) for local turns.
    Config "llm.history_budget" (default 5000). Clamped so it always leaves
    ~3k tokens of headroom inside num_ctx for the reply and estimate error,
    so raising it never needs more VRAM than the existing num_ctx already uses."""
    try:
        from core.llm_client import _load_config, _num_ctx
        want = int(_load_config().get("llm.history_budget", 5000))
        return max(2000, min(want, _num_ctx() - 3000))
    except Exception:
        return 3000
from core.llm_client import (
    call_llm_stream,
    get_llm_provider,
    get_gemini_settings,
    GEMINI_BASE_URL,
    get_context_window,
    get_llm_settings,
    get_tier_model_settings,
)
from core import degrade as _degrade

logger = logging.getLogger("aether")


def _local_tier_stream_route(tier: _Tier, provider: str) -> tuple[dict, str, str, str]:
    if provider != "ollama":
        return {}, "default", "", ""
    url, model, status, requested = get_tier_model_settings(tier.value)
    if status == "default":
        return {}, status, model, requested
    return {
        "provider_override": "ollama",
        "url_override": url,
        "model_override": model,
    }, status, model, requested


def _load_cfg_for_degrade() -> dict:
    try:
        from core.llm_client import _load_config
        return _load_config()
    except Exception:  # noqa: BLE001
        return {}

# Matches assistant text that is ENTIRELY a call-looking expression, e.g.
# `save_memory(category="greetings", key="hello", value="")`. Anchored at
# both ends so ordinary prose containing parentheses ("Sure! (one sec)")
# never matches — only a response that is nothing but a bare call.
_FAKE_TOOL_CALL_RE = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_]*)\((.*)\)\s*$', re.DOTALL)
_FAKE_TOOL_KWARG_RE = re.compile(
    r'(\w+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^,")\']+))'
)


def _parse_fake_tool_call(text: str) -> tuple[str, dict] | None:
    """Best-effort recovery for a common small/weak-local-model failure
    mode: instead of using structured tool calling, the model writes the
    call out as plain text (e.g. llama3.2:3b doing this for save_memory).
    Without this, that raw text -- "save_memory(category=..., key=...)" --
    gets displayed and spoken to the user verbatim as if it were an
    actual answer, which is exactly what happened before this existed.

    Returns (function_name, kwargs) if `text`, once stripped, is nothing
    but a single call-looking expression; otherwise None. Deliberately
    conservative — see _FAKE_TOOL_CALL_RE's anchoring.
    """
    m = _FAKE_TOOL_CALL_RE.match(text.strip())
    if not m:
        return None
    name, argtext = m.group(1), m.group(2)
    args: dict = {}
    for km in _FAKE_TOOL_KWARG_RE.finditer(argtext):
        key = km.group(1)
        val = km.group(2)
        if val is None:
            val = km.group(3)
        if val is None:
            val = km.group(4)
        args[key] = val.strip() if isinstance(val, str) else val
    return name, args


# Qwen/Hermes-style text tool calls: <tool_call>{"name": ..., "arguments": {...}}</tool_call>
# Ollama normally converts these into structured tool_calls itself; when its
# parser misses one the raw tag text would otherwise be shown and spoken.
_TAGGED_BLOCK_RE = re.compile(r'<tool_call>\s*(.*?)\s*(?:</tool_call>|$)', re.DOTALL)
_FENCED_JSON_RE = re.compile(r'^```(?:json|tool_call)?\s*(\{.*\})\s*```$', re.DOTALL)

# Names weak models invent for volume control -> the real tool (computer_settings).
_VOLUME_ALIASES = {"set_volume", "volume_control", "volume", "change_volume",
                   "adjust_volume", "volume_set", "system_volume"}


def _call_from_obj(obj) -> tuple[str, dict] | None:
    if not isinstance(obj, dict) or not isinstance(obj.get("name"), str):
        return None
    args = obj.get("arguments", obj.get("parameters", {}))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (ValueError, TypeError):
            return None
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None
    return obj["name"], args


def _normalize_recovered_call(name: str, args: dict) -> tuple[str, dict]:
    """Map invented volume tool names onto computer_settings."""
    if name.lower() in _VOLUME_ALIASES:
        act = str(args.get("action", "")).lower().replace(" ", "_")
        if act in ("volume_up", "volume_down", "mute", "unmute", "toggle_mute"):
            return "computer_settings", {"action": act}
        level = next((args[k] for k in ("level", "value", "volume", "percent", "amount")
                      if k in args and args[k] not in (None, "")), None)
        if level is not None:
            return "computer_settings", {"action": "volume_set", "value": level}
    return name, args


def _parse_tagged_tool_calls(text: str) -> list[tuple[str, dict]] | None:
    """Recover tool calls a model wrote as text instead of calling them.

    Accepted shapes (anything else returns None, so ordinary replies are never
    reinterpreted):
      * one or more <tool_call>{json}</tool_call> blocks, with optional prose
        around them as long as the tags are not inside a ``` code fence
        (a fenced tag is an explanation, not a call);
      * a reply that is ONLY a bare {"name":..., "arguments":...} JSON object,
        optionally wrapped in a single ``` / ```json fence.
    Every block must parse, or the whole reply is rejected. The caller still
    checks every name against the known-tool allowlist.
    """
    t = (text or "").strip()
    if not t:
        return None
    if "<tool_call>" in t:
        if "```" in t:
            return None
        calls = []
        for m in _TAGGED_BLOCK_RE.finditer(t):
            body = m.group(1).strip()
            if not body:
                continue
            try:
                call = _call_from_obj(json.loads(body))
            except (ValueError, TypeError):
                return None
            if call is None:
                return None
            calls.append(_normalize_recovered_call(*call))
        return calls or None
    fm = _FENCED_JSON_RE.match(t)
    if fm:
        t = fm.group(1)
    if t.startswith("{") and t.endswith("}"):
        try:
            call = _call_from_obj(json.loads(t))
        except (ValueError, TypeError):
            return None
        return [_normalize_recovered_call(*call)] if call else None
    return None


class ConversationCoreMixin:
    """Owns the per-turn pipeline: context prep, tool-loop, finalize.

    Mixed into AetherLocal — see module docstring above.
    """

    def _prepare_conversation_messages(self, user_text: str, light: bool = False, tools: list | None = None,
                                       history_budget: int | None = None):
        """Log the incoming message, store it to memory, trim history, and
        build the compressed message list to send to the LLM.

        `light=True` (small-talk turns per tier_router) skips the expensive
        system-prompt additions — see `_build_system_prompt`.

        `tools` should be the tool schema list that will actually be sent
        alongside these messages this turn (or None/[] for a no-tools
        turn) — it's used to reserve budget for the tool schema so the
        3000-token compression target reflects the real request size, not
        just message content. See estimate_tool_tokens.

        Returns (messages, _active_mem) — `_active_mem` is whichever memory
        engine (hybrid or semantic) is active, or None, so the caller can
        later store the full exchange once the reply is known.
        """
        self.ui.set_state("THINKING")
        self.ui.write_log(f"You: {user_text}")

        # ── Store to memory — only one engine writes, never both ───────
        # hybrid_mem supersedes semantic_mem; if both exist only hybrid writes.
        _active_mem = self._hybrid_mem or self._semantic_mem
        if _active_mem:
            try:
                _active_mem.add_message("user", user_text)
            except Exception:
                pass

        self._conversation.append({"role": "user", "content": user_text})

        # Default aligned with config/config.example.json's documented
        # default (100) — was 12 here, which is what actually applied
        # whenever a config (like the shipped config/api_keys.json) didn't
        # set the key explicitly, silently truncating history far below the
        # documented behavior.
        MAX_HISTORY = int(self._config.get("memory.chat_history_limit", 100))
        if len(self._conversation) > MAX_HISTORY:
            trimmed = self._conversation[-MAX_HISTORY:]
            # Never let the trim start on a dangling "tool" message — it must
            # stay paired with the preceding assistant message that issued the
            # tool_calls, or the next LLM request has an invalid message
            # sequence (API error / stuck round).
            while trimmed and trimmed[0].get("role") == "tool":
                trimmed = trimmed[1:]
            self._conversation = trimmed

        messages = [
            {"role": "system", "content": self._build_system_prompt(user_text, light=light)}
        ] + list(self._conversation)

        # ── NEW: compress context to fit token window ─────────────────
        # Reserve budget for the tool schema this turn will actually carry —
        # otherwise a request judged "under 3000 tokens" on message content
        # alone can still blow the model's real context once several
        # thousand tokens of tool declarations are attached (see
        # estimate_tool_tokens). Floor at 500 so a huge tool schema can't
        # wipe the entire conversation; when the floor kicks in, that's a
        # genuine "3000 total isn't enough for this many tools" situation
        # worth surfacing, not silently absorbing.
        _tool_tokens = estimate_tool_tokens(tools)
        # history_budget is the MESSAGE budget; cloud (Gemini) turns pass a larger
        # one since that window is huge, local turns keep 3000 total with the schema.
        if history_budget is None:
            history_budget = _local_total_budget()
        _budget = history_budget if history_budget >= _CLOUD_HISTORY_BUDGET else history_budget - _tool_tokens
        if _budget < 500:
            logger.warning(
                "smart_context: tool schema alone is ~%d tokens — "
                "clamping message budget to floor (500) instead of the "
                "nominal %d remaining", _tool_tokens, _budget,
            )
            _budget = 500

        from main import _LANGCHAIN_OK  # lazy: avoids circular import at module load time
        if _LANGCHAIN_OK:
            try:
                from integrations.langchain_tools import lc_compress_history
                messages = lc_compress_history(messages, max_tokens=_budget)
            except Exception:
                messages = compress_if_needed(messages, token_budget=_budget)
        else:
            messages = compress_if_needed(messages, token_budget=_budget)

        return messages, _active_mem

    def _execute_tool_calls(self, final_tool_calls: list, messages: list):
        """Run every tool call the LLM asked for in this round.

        Appends one "tool" message per call to both `messages` (in place)
        and `self._conversation`. Returns (_tool_results, all_silent):
        `_tool_results` is the list of (tool_name, result) pairs for calls
        that weren't `__SILENT__`; `all_silent` is True iff every call in
        this round returned `__SILENT__`.
        """
        all_silent    = True
        _tool_results: list[tuple[str, str]] = []
        self.ui.set_state("PROCESSING")

        for tc in final_tool_calls:
            fn    = tc.get("function", {})
            tname = fn.get("name", "")
            targs = fn.get("arguments", {})
            if isinstance(targs, str):
                try:
                    targs = json.loads(targs)
                except Exception:
                    targs = {}

            tc_id = tc.get("id", "")
            _arg_hint = ""
            if isinstance(targs, dict) and targs:
                _first_k = next(iter(targs))
                _first_v = str(targs[_first_k])[:40]
                _arg_hint = f" ({_first_k}={_first_v})"
            self.ui.write_log(f"SYS: ▶ {tname}{_arg_hint}")

            # ── NEW v2: loop guard — block if tool called too many times ──
            if self._loop_guard:
                verdict = self._loop_guard.check(tname, targs)
                if verdict.blocked:
                    result = self._loop_guard.inject_break_message(tname)
                    self.ui.write_log(f"WRN: LoopGuard blocked {tname}")
                    self._obs.count("loop_guard.blocked", tags={"tool": tname})
                else:
                    with self._obs.span("tool_call", tool=tname) as span:
                        result = self._execute_tool(tname, targs)
                        span.set("result_len", len(str(result)))
                    self._loop_guard.record(tname, targs)
                    self._obs.count("tool.calls", tags={"tool": tname})
            else:
                with self._obs.span("tool_call", tool=tname):
                    result = self._execute_tool(tname, targs)
                    self._obs.count("tool.calls", tags={"tool": tname})

            if result != "__SILENT__":
                all_silent = False
                _tool_results.append((tname, result))

            tool_msg: dict = {
                "role":    "tool",
                "content": "Done." if result == "__SILENT__" else str(result),
            }
            if tc_id:
                tool_msg["tool_call_id"] = tc_id

            messages.append(tool_msg)
            self._conversation.append(tool_msg)

        return _tool_results, all_silent

    def _tools_for_current_turn(self) -> list:
        """Build the tool list dynamically so hot-reloaded plugins are visible."""
        from main import OLLAMA_TOOLS, _to_ollama_tools  # lazy: avoids circular import at module load time
        tools = list(OLLAMA_TOOLS)
        # open_interpreter runs model-written code. The integration is documented as
        # OFF by default (integrations.open_interpreter), but it used to be offered to
        # the model on every turn regardless -- the flag only controlled the pip install.
        # Only advertise it when the user has explicitly enabled it.
        if not self._config.get("integrations.open_interpreter", False):
            tools = [tl for tl in tools
                     if tl.get("function", {}).get("name") != "open_interpreter"]
        if self._mcp:
            try:
                if self._mcp.list_all_tools():
                    from core.tool_declarations import MCP_TOOL_DECLARATION
                    tools.extend(_to_ollama_tools([MCP_TOOL_DECLARATION]))
            except Exception as e:
                self.ui.write_log(f"WRN: MCP tool declaration unavailable - {e}")
        if not self._plugins:
            return tools
        try:
            core_names = {
                tool.get("function", {}).get("name")
                for tool in OLLAMA_TOOLS
            }
            declarations = [
                d for d in self._plugins.get_tool_declarations()
                if d.get("name") not in core_names
            ]
            if declarations:
                tools.extend(_to_ollama_tools(declarations))
        except Exception as e:
            self.ui.write_log(f"WRN: Plugin tool declarations unavailable - {e}")
        return tools

    def _try_quick_intent(self, user_text: str) -> bool:
        """Run simple commands directly. True if the turn was fully handled."""
        from core.quick_intents import match_all
        plan = match_all(user_text)
        if not plan:
            return False
        _t0 = time.monotonic()
        replies: list[str] = []
        for name, args, friendly in plan:
            self.ui.write_log(f"SYS: quick intent -> {name} {args}")
            try:
                result = self._execute_tool(name, args)
            except Exception as e:  # noqa: BLE001
                result = f"That failed: {e}"
            replies.append(friendly(result) if friendly else str(result))
        reply = " ".join(r for r in replies if r).strip() or "Done."
        try:
            from core.live_report import record_turn
            record_turn(user_text=user_text, reply=reply, tier="QUICK_INTENT",
                        tier_reason=",".join(n for n, _, _ in plan), tools_sent=0,
                        rounds_used=0, latency_ms=(time.monotonic() - _t0) * 1000)
        except Exception:
            pass
        with self._conversation_lock:
            self._conversation.append({"role": "user", "content": user_text})
            self._conversation.append({"role": "assistant", "content": reply})
        self.ui.write_log(f"Aether: {reply}")
        self.speak(reply)
        try:
            self._obs.count("quick_intent.handled")
        except Exception:
            pass
        if not self.ui.muted:
            self.ui.set_state("LISTENING")
        return True

    def _process_message_inner(self, user_text: str) -> None:
        # ── NEW v2: reset loop guard for new request ──────────────────
        if self._loop_guard:
            self._loop_guard.reset()
        # New request: ends any tool approvals granted for the previous one (core/risk.py
        # "task" tier). "[SYSTEM_ALERT]" is only a text prefix that the dashboard, a mic
        # transcript or pasted text can also produce, so an alert turn must NOT inherit
        # the user's earlier approval: it starts its own turn, labelled as an alert.
        try:
            from core import risk as _risk
            if str(user_text).lstrip().startswith("[SYSTEM_ALERT]"):
                _risk.begin_turn("[system alert - not a user request]")
            else:
                _risk.begin_turn(user_text)
        except Exception:  # noqa: BLE001 - never let this break a turn
            pass
        # Track message in observability
        self._obs.count("messages.received")
        # ── NEW: handle built-in chat commands ────────────────────────
        if self._handle_chat_command(user_text):
            return

        # ── Deterministic shortcuts for simple commands (volume, weather, ──
        # list files, open app). Small local models invent tool names/actions
        # or answer from imagination; these phrases need no model at all.
        if self._try_quick_intent(user_text):
            return

        # ── Fast path for trivial turns (greetings, "thanks", "ok", …) ──
        # Skips the full tool schema and the expensive system-prompt
        # additions (memory retrieval, goals, MCP/plugin/integration
        # inventories) that were previously sent on every single turn
        # regardless of complexity. Follow-ups inside an active goal never
        # get downgraded, so an in-progress task isn't interrupted.
        _turn_start = time.monotonic()  # for live_report — whole-turn wall time
        _has_active_task = bool(self._goals and self._goals.list_active())
        _route = _classify_tier(user_text, has_active_task=_has_active_task)
        _is_quick = _route.tier == _Tier.QUICK

        # ── HARD tier: escalate this turn to Gemini ─────────────────────
        # Only relevant when the *primary* configured provider is local
        # (Ollama/LM Studio) — if Gemini is already the primary provider
        # there's nothing to escalate to. Silently no-ops (stays on the
        # default model) if no gemini_api_key is configured, rather than
        # failing the turn.
        _stream_kwargs: dict = {}
        _primary_provider = get_llm_provider()
        from core.leak_guard import cloud_allowed as _cloud_allowed
        _cloud_ok = _cloud_allowed()
        if not _cloud_ok and _route.tier == _Tier.HARD:
            self.ui.write_log("SYS: cloud_allow=false - staying on the local model for this turn")
        if _cloud_ok and _route.tier == _Tier.HARD and _primary_provider != "gemini":
            _gem_key, _gem_model = get_gemini_settings()
            if _gem_key:
                _stream_kwargs = {
                    "provider_override": "gemini",
                    "url_override":      GEMINI_BASE_URL,
                    "model_override":    _gem_model,
                }
                self.ui.write_log(f"SYS: routing this turn to Gemini ({_route.reason})")
            else:
                self.ui.write_log("WRN: complex-reasoning tier wanted Gemini, but no gemini_api_key is configured — staying on the default model")

        # ── Opt-in cloud route for tool turns (gemini_for_tools=true) ─────
        # Off by default. When on, tool-using turns go to Gemini because small
        # local models are unreliable at structured tool calls. This sends the
        # message, injected memory and tool results to Google, so every such
        # turn is logged visibly.
        if _cloud_ok and not _stream_kwargs:
            from core.llm_client import get_config_flag as _cfg_flag
            _gem_key2, _gem_model2 = get_gemini_settings()
            if _wants_gemini_tools(user_text, flag=_cfg_flag("gemini_for_tools"),
                                   provider=_primary_provider, has_key=bool(_gem_key2),
                                   tier=_route.tier):
                _stream_kwargs = {
                    "provider_override": "gemini",
                    "url_override":      GEMINI_BASE_URL,
                    "model_override":    _gem_model2,
                }
                self.ui.write_log("SYS: tool turn routed to Gemini (cloud, opt-in gemini_for_tools)")

        # Optional tier-specific models are supported for Ollama. The resolver
        # checks the selected local/remote host and falls back to its configured
        # default model if the requested tag is absent or the host cannot be
        # checked. Hosted and OpenAI-compatible providers keep their own routing.
        if not _stream_kwargs:
            (_local_route, _tier_status, _tier_model, _requested_model) = (
                _local_tier_stream_route(_route.tier, _primary_provider)
            )
            _stream_kwargs.update(_local_route)
            if _tier_status == "configured":
                self.ui.write_log(
                    f"SYS: using {_route.tier.value} tier model {_tier_model}"
                )
            elif _tier_status in ("missing", "unavailable"):
                self.ui.write_log(
                    f"WRN: {_route.tier.value} tier model {_requested_model} "
                    f"is {_tier_status}; using configured model {_tier_model}"
                )

        # Tool exposure scales with tier: QUICK gets none (see above),
        # EVERYDAY gets only the category-relevant subset (core/tool_router)
        # instead of the full ~28-tool catalog on every ordinary turn, and
        # HARD gets a wider toolkit (core/tool_router._HARD_EXTRA) — the full
        # ~31-tool catalog overran the message budget and crowded out history.
        if _is_quick:
            _turn_tools = []
        elif _route.tier == _Tier.HARD:
            _turn_tools = _filter_tools_hard(self._tools_for_current_turn(), user_text)
        else:
            _turn_tools = _filter_tools_for_turn(self._tools_for_current_turn(), user_text)
        messages, _active_mem = self._prepare_conversation_messages(
            user_text, light=_is_quick, tools=_turn_tools,
            history_budget=(_CLOUD_HISTORY_BUDGET
                            if _stream_kwargs or _primary_provider in ("gemini", "openai_api")
                            else None),
        )
        # For live_report: did memory injection actually surface anything?
        # None means "not attempted" (QUICK-tier turns skip retrieval
        # entirely — see _build_system_prompt's light=True path).
        _memory_hit = (
            None if _is_quick else
            "[Relevant memory]" in (messages[0].get("content", "") if messages else "")
        )

        # _NEEDS_LLM_ROUND is defined at module level

        MAX_TOOL_ROUNDS = 6
        final_assistant_reply = ""
        _llm_synthesized = False   # True only when the LLM produced the final reply
        _tool_results:  list[tuple[str, str]] = []  # FIX: must be defined before loop
        # Counts consecutive rounds where every tool call was silent (e.g. a
        # bare save_memory with no accompanying text) — used to give the model
        # one real chance to answer before falling back to a canned ack, so a
        # compound message ("remember X, also what should I improve?") doesn't
        # get its real question thrown away. See all_silent handling below.
        _consecutive_silent_rounds = 0
        _any_truncated = False   # for live_report — set if any round hit num_predict
        _loop_exhausted = False  # for live_report — set in the exhaustion branch below
        _turn_cancelled = False

        tools = _turn_tools
        _empty_retried = False   # one no-tools retry after an empty model response

        for _round in range(MAX_TOOL_ROUNDS):
            if self._cancel_current_turn.is_set():
                _turn_cancelled = True
                break
            final_content    = ""
            final_tool_calls: list = []
            _streamed: list[str] = []
            _llm_stats: dict = {}

            _round_start = time.monotonic()
            _wait_done = False
            for _attempt in range(_degrade.MAX_ATTEMPTS):
                try:
                    for event in call_llm_stream(
                            messages, tools, cancel_event=self._cancel_current_turn,
                            **_stream_kwargs):
                        if event["type"] == "sentence":
                            if self._cancel_current_turn.is_set():
                                _turn_cancelled = True
                                break
                            _streamed.append(event["text"])
                        elif event["type"] == "done":
                            final_content    = event["content"]
                            final_tool_calls = event["tool_calls"]
                            _llm_stats       = event.get("stats", {})
                        elif event["type"] == "cancelled":
                            _turn_cancelled = True
                            final_content = event.get("content", "")
                            break
                    break
                except RuntimeError as e:
                    if self._cancel_current_turn.is_set():
                        _turn_cancelled = True
                        break
                    if _stream_kwargs and _attempt == 0:
                        # Cloud route failed (bad key, quota, 400...): never leave the
                        # user with no answer -- retry this round on the local model.
                        self.ui.write_log(f"WRN: Gemini failed ({e}) - retrying this turn on the local model")
                        _stream_kwargs = {}
                        # local window is small: re-fit history to the local budget
                        messages = compress_if_needed(
                            messages, token_budget=max(500, _local_total_budget() - estimate_tool_tokens(tools)))
                        _streamed.clear()
                        final_content, final_tool_calls, _llm_stats = "", [], {}
                        continue
                    # Local backend failed: classify and follow the recovery policy
                    # (retry once, fallback model, opt-in cloud) instead of giving up.
                    _fail = _degrade.classify(e)
                    _cfg_now = _load_cfg_for_degrade()
                    _gkey, _gmodel = get_gemini_settings()
                    _act = _degrade.plan_recovery(
                        _fail, _attempt, _cfg_now,
                        current_model=(_stream_kwargs.get("model_override") or get_llm_settings()[1]),
                        on_cloud=bool(_stream_kwargs) and _stream_kwargs.get("provider_override") == "gemini",
                        has_gemini_key=bool(_gkey))
                    if _act.kind != "give_up":
                        self.ui.write_log(f"WRN: LLM failed ({e}) - {_act.note}")
                        self._obs.count(f"llm.recover.{_act.kind}")
                        _streamed.clear()
                        final_content, final_tool_calls, _llm_stats = "", [], {}
                        if _act.kind == "wait_retry":
                            if self._cancel_current_turn.wait(_act.wait_s):
                                _turn_cancelled = True
                                break
                        elif _act.kind == "switch_model":
                            _stream_kwargs = {"provider_override": "ollama",
                                              "url_override": get_llm_settings()[0],
                                              "model_override": _act.model}
                        elif _act.kind == "cloud":
                            _stream_kwargs = {"provider_override": "gemini",
                                              "url_override": GEMINI_BASE_URL,
                                              "model_override": _gmodel}
                            messages = compress_if_needed(
                                messages, token_budget=max(500, _CLOUD_HISTORY_BUDGET - estimate_tool_tokens(tools)))
                        continue
                    self.ui.write_log(f"ERR: LLM - {e}")
                    self.ui.write_log(f"Aether: {_fail.message}")
                    self.speak(_fail.message)
                    if not self.ui.muted:
                        self.ui.set_state("LISTENING")
                    return
            _round_elapsed = time.monotonic() - _round_start

            if self._cancel_current_turn.is_set():
                _turn_cancelled = True
            if _turn_cancelled:
                partial_reply = (final_content or " ".join(_streamed)).strip()
                if partial_reply:
                    partial_msg = {"role": "assistant", "content": partial_reply}
                    messages.append(partial_msg)
                    self._conversation.append(partial_msg)
                    self.ui.write_log(f"Aether: {partial_reply} *(stopped)*")
                self.ui.write_log("SYS: Response stopped.")
                final_assistant_reply = partial_reply
                break

            # Empty round: no text, no tool call (small local models do this when a
            # tool-laden prompt confuses them, e.g. "What is 240 times 17?" on 9 tools).
            # Retry ONCE with no tools so the model has to just answer, instead of
            # falling through to the "wasn't able to finish" message.
            if (not final_tool_calls and not (final_content or "").strip()
                    and not _streamed and not _empty_retried):
                _empty_retried = True
                self.ui.write_log("WRN: model returned an empty response — retrying once without tools")
                self._obs.count("llm.empty_response_retry")
                tools = None
                continue

            # done_reason=="length" means generation hit num_predict and was
            # cut off mid-thought, not that the model actually finished — make
            # that visible instead of silently presenting a chopped answer.
            _was_truncated = bool(_llm_stats.get("truncated"))
            if _was_truncated:
                final_content = final_content.rstrip() + " …"
                self.ui.write_log("WRN: response truncated at generation limit")
                _any_truncated = True

            if not final_tool_calls and final_content:
                _tagged = _parse_tagged_tool_calls(final_content)
                if _tagged:
                    _always_ok = (
                        "save_memory", "goal_create",
                        "goal_update", "goal_complete", "goal_cancel",
                        "goal_list", "goal_add_subtask",
                    )
                    _disp = getattr(self, "_TOOL_DISPATCH", {})
                    if all(n in _disp or n in _always_ok for n, _ in _tagged):
                        self.ui.write_log(
                            "WRN: model wrote <tool_call> as text instead of "
                            "calling it — executing it anyway"
                        )
                        for _n, _ in _tagged:
                            self._obs.count("llm.fake_tool_call_recovered", tags={"tool": _n})
                        final_tool_calls = [
                            {"id": "", "function": {"name": _n, "arguments": _a}}
                            for _n, _a in _tagged
                        ]
                        # The call text must not be shown/spoken as the reply.
                        final_content = _TAGGED_BLOCK_RE.sub("", final_content).strip()
                    else:
                        self.ui.write_log(
                            "WRN: model wrote <tool_call> for an unrecognized "
                            "tool — discarding"
                        )
                        self._obs.count("llm.fake_tool_call_unrecoverable")
                        final_content = ""
                if (not _tagged and final_content and
                        ("<tool_call" in final_content or
                         (final_content.lstrip().startswith(("{", "```")) and '"name"' in final_content))):
                    self.ui.write_log(
                        "WRN: reply looks like a tool call but could not be parsed: "
                        + final_content[:200].replace("\n", " ")
                    )
                _fake = None if (_tagged or not final_content) else _parse_fake_tool_call(final_content)
                if _fake:
                    _fname, _fargs = _fake
                    _known_tool = (
                        _fname in getattr(self, "_TOOL_DISPATCH", {})
                        or _fname in (
                            "save_memory", "goal_create",
                            "goal_update", "goal_complete", "goal_cancel",
                            "goal_list", "goal_add_subtask",
                        )
                    )
                    if _known_tool:
                        # Recover by actually running the tool the model
                        # clearly intended, instead of showing raw call
                        # syntax to the user as if it were a spoken reply.
                        self.ui.write_log(
                            f"WRN: model wrote '{_fname}' as text instead of "
                            f"calling it — executing it anyway"
                        )
                        self._obs.count("llm.fake_tool_call_recovered", tags={"tool": _fname})
                        final_tool_calls = [{"id": "", "function": {"name": _fname, "arguments": _fargs}}]
                        # Whole reply was the call expression -- don't echo it as text
                        # (the save_memory-only branch below would otherwise print/speak it).
                        final_content = ""
                    else:
                        # Unknown/unparseable target — don't surface it.
                        # Clearing final_content here (with final_tool_calls
                        # still empty) falls through to the same
                        # loop-exhaustion handling used when the model
                        # produces nothing at all, which already gives a
                        # clean fallback message instead of raw text.
                        self.ui.write_log(
                            f"WRN: model produced call-like text for an "
                            f"unrecognized tool ('{_fname}') — discarding"
                        )
                        self._obs.count("llm.fake_tool_call_unrecoverable")
                        final_content = ""

            if not final_tool_calls:
                if _streamed:
                    for sentence in _streamed:
                        self.speak(sentence)
                    assistant_msg = {"role": "assistant", "content": final_content}
                    messages.append(assistant_msg)
                    self._conversation.append(assistant_msg)
                    self.ui.write_log(f"Aether: {final_content}")
                    if _was_truncated:
                        self.speak(" …")
                    final_assistant_reply = final_content
                    _llm_synthesized = True
                    self._publish_message_stats(_llm_stats, _round_elapsed, _stream_kwargs)
                elif final_content:
                    assistant_msg = {"role": "assistant", "content": final_content}
                    messages.append(assistant_msg)
                    self._conversation.append(assistant_msg)
                    self.ui.write_log(f"Aether: {final_content}")
                    self.speak(final_content)
                    final_assistant_reply = final_content
                    _llm_synthesized = True
                    self._publish_message_stats(_llm_stats, _round_elapsed, _stream_kwargs)
                break

            if self._cancel_current_turn.is_set():
                _turn_cancelled = True
                partial_reply = (final_content or " ".join(_streamed)).strip()
                if partial_reply:
                    partial_msg = {"role": "assistant", "content": partial_reply}
                    messages.append(partial_msg)
                    self._conversation.append(partial_msg)
                    self.ui.write_log(f"Aether: {partial_reply} *(stopped)*")
                self.ui.write_log("SYS: Response stopped before the requested action started.")
                final_assistant_reply = partial_reply
                break

            assistant_msg = {
                "role":       "assistant",
                "content":    final_content or "",
                "tool_calls": final_tool_calls,
            }
            messages.append(assistant_msg)
            self._conversation.append(assistant_msg)

            _only_memory = all(
                tc.get("function", {}).get("name") == "save_memory"
                for tc in final_tool_calls
            )
            if _only_memory and final_content:
                for tc in final_tool_calls:
                    fn    = tc.get("function", {})
                    targs = fn.get("arguments", {})
                    if isinstance(targs, str):
                        try:
                            targs = json.loads(targs)
                        except Exception:
                            targs = {}
                    self._execute_tool("save_memory", targs)
                assistant_msg2 = {"role": "assistant", "content": final_content}
                messages.append(assistant_msg2)
                self._conversation.append(assistant_msg2)
                self.ui.write_log(f"Aether: {final_content}")
                if not _streamed:
                    self.speak(final_content)
                final_assistant_reply = final_content
                break

            _tool_results, all_silent = self._execute_tool_calls(final_tool_calls, messages)

            if self._cancel_current_turn.is_set():
                _turn_cancelled = True
                self.ui.write_log("SYS: Response stopped after the active action finished.")
                break

            if not self.ui.muted:
                self.ui.set_state("THINKING")

            if all_silent:
                _consecutive_silent_rounds += 1
                _rounds_left = MAX_TOOL_ROUNDS - 1 - _round
                # Give the model one real shot at answering with the tool
                # result now in context — a silent save_memory call often
                # accompanies a real question the user still wants answered.
                # Only fall back to a canned ack if the model keeps calling
                # tools silently without ever producing text, or we're out
                # of rounds.
                if _consecutive_silent_rounds < 2 and _rounds_left > 0:
                    continue

                _saved_name: str | None = None
                for _tc in final_tool_calls:
                    _fn = _tc.get("function", {})
                    if _fn.get("name") == "save_memory":
                        _a = _fn.get("arguments", {})
                        if isinstance(_a, str):
                            try:
                                _a = json.loads(_a)
                            except Exception:
                                _a = {}
                        if isinstance(_a, dict) and _a.get("key") == "name" and _a.get("value"):
                            _saved_name = str(_a["value"])
                            break
                _ack = f"Got it, {_saved_name}." if _saved_name else "Noted."
                _amsg = {"role": "assistant", "content": _ack}
                messages.append(_amsg)
                self._conversation.append(_amsg)
                self.ui.write_log(f"Aether: {_ack}")
                self.speak(_ack)
                final_assistant_reply = _ack
                break
            else:
                _consecutive_silent_rounds = 0

            from main import _NEEDS_LLM_ROUND  # lazy: avoids circular import at module load time
            if _tool_results and not any(n in _NEEDS_LLM_ROUND for n, _ in _tool_results):
                _, _reply = _tool_results[-1]
                _amsg = {"role": "assistant", "content": _reply}
                messages.append(_amsg)
                self._conversation.append(_amsg)
                self.ui.write_log(f"Aether: {_reply}")
                self.speak(_reply)
                final_assistant_reply = _reply
                break

        # ── Tool loop exhausted without ever producing a reply ──────────
        # Every round returned a tool call that needed another LLM round
        # (so no round's `not any(... in _NEEDS_LLM_ROUND)` check broke the
        # loop) and none were silent-only, so the for-loop ran out of
        # MAX_TOOL_ROUNDS with final_assistant_reply still "". Previously
        # this fell straight through to _finalize_response with nothing to
        # say — the user got total silence with no error, log line, or
        # spoken response. Surface it instead.
        if not final_assistant_reply and not _turn_cancelled:
            _loop_exhausted = True
            final_assistant_reply = (
                "I wasn't able to finish that after a few tool attempts — "
                "want me to try a different approach?"
            )
            _exhausted_msg = {"role": "assistant", "content": final_assistant_reply}
            messages.append(_exhausted_msg)
            self._conversation.append(_exhausted_msg)
            self.ui.write_log(f"WRN: tool-call loop exhausted after {MAX_TOOL_ROUNDS} rounds without a reply")
            self.ui.write_log(f"Aether: {final_assistant_reply}")
            self.speak(final_assistant_reply)

        # ── Live usage dump — logs/live_turns.jsonl + logs/live_report.md ──
        # Best-effort, never raises; see core/live_report.py.
        from core.live_report import record_turn
        record_turn(
            user_text=user_text, reply=final_assistant_reply,
            tier=_route.tier.name, tier_reason=_route.reason,
            tools_sent=len(_turn_tools), memory_hit=_memory_hit,
            gemini_escalated=bool(_stream_kwargs), rounds_used=_round + 1,
            exhausted=_loop_exhausted, truncated=_any_truncated,
            latency_ms=(time.monotonic() - _turn_start) * 1000,
        )

        # ── Store full exchange + self-evaluate ─────────────────────────
        self._finalize_response(user_text, "" if _turn_cancelled else final_assistant_reply, _active_mem,
                                 _tool_results, _llm_synthesized)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

    # ------------------------------------------------------------------
    # STT loops
    # ------------------------------------------------------------------

    def _finalize_response(self, user_text: str, final_assistant_reply: str,
                            _active_mem, _tool_results: list, _llm_synthesized: bool) -> None:
        """After the tool-call loop ends: store the full exchange to memory
        and, if applicable, run the self-evaluator on the LLM's reply."""
        # ── Store full exchange — only the active engine ───────────────
        from core.memory_hygiene import is_storable_exchange
        if _active_mem and final_assistant_reply and is_storable_exchange(user_text, final_assistant_reply):
            try:
                _active_mem.store(user_text, final_assistant_reply)
            except Exception:
                pass

        # ── NEW v2: self-evaluate the response ────────────────────────
        # Only evaluate when the LLM produced the final reply; skip when we
        # used a raw tool result directly (score would be meaningless noise).
        if self._evaluator and final_assistant_reply and _tool_results and _llm_synthesized:
            try:
                # Fast heuristic check first — skip expensive LLM eval if
                # the response already looks like an obvious failure
                if not self._evaluator.quick_check(final_assistant_reply):
                    self._obs.count("eval.quick_fail")
                    self.ui.write_log("WRN: Evaluator — quick check flagged response as likely failure")
                else:
                    eval_result = self._evaluator.evaluate(
                        user_text, final_assistant_reply, _tool_results)
                    self._obs.count("eval.passed" if eval_result.passed else "eval.failed")
                    if not eval_result.passed:
                        self.ui.write_log(
                            f"WRN: Evaluator — {eval_result.reason} "
                            f"(score={eval_result.score:.2f})")
                        self._obs.log("eval_fail",
                                      request=user_text[:80],
                                      reason=eval_result.reason,
                                      score=eval_result.score)
            except Exception:
                pass

    def _publish_message_stats(self, stats: dict, elapsed: float, stream_kwargs: dict | None = None) -> None:
        """Sends model/token/timing info from the last LLM round to the
        Message Stats panel. Best-effort — any missing field is shown as
        '—' by the UI rather than a misleading 0."""
        try:
            model         = stats.get("model")
            prompt_tokens = stats.get("prompt_tokens")
            output_tokens = stats.get("output_tokens")
            eval_duration = stats.get("eval_duration")  # ns, model-generation time only

            tokens_per_sec = None
            if output_tokens:
                if eval_duration:
                    secs = eval_duration / 1e9
                    if secs > 0:
                        tokens_per_sec = output_tokens / secs
                elif elapsed > 0:
                    tokens_per_sec = output_tokens / elapsed

            try:
                from core import perf_stats
                perf_stats.record(model, perf_stats.route_for(stream_kwargs), prompt_tokens,
                                  output_tokens, eval_duration, elapsed,
                                  ttft_s=stats.get("ttft_s"))
            except Exception:
                pass

            try:
                context_window = get_context_window(model)
            except Exception:
                context_window = None

            self.ui.update_message_stats({
                "model":          model,
                "prompt_tokens":  prompt_tokens,
                "output_tokens":  output_tokens,
                "elapsed":        elapsed,
                "tokens_per_sec": tokens_per_sec,
                "ttft_s":         stats.get("ttft_s"),
                "context_window": context_window,
            })
        except Exception:
            pass  # stats panel is best-effort, never break the response

    def _process_message(self, user_text: str) -> None:
        self._last_user_speech = time.monotonic()
        # Serialize full turns across threads (see _conversation_lock comment
        # in __init__). A blocking acquire here is intentional: a proactive
        # nudge or a system-monitor alert should queue up behind a message
        # that's mid-flight rather than race it.
        with self._conversation_lock:
            self._process_message_locked(user_text)

    def _process_message_locked(self, user_text: str) -> None:
        self._cancel_current_turn.clear()
        try:
            self._process_message_inner(user_text)
        except Exception as e:
            from main import log_crash  # lazy: avoids circular import at module load time
            log_crash(e, user_message=user_text)
            traceback.print_exc()
            error_msg = f"A critical error occurred: {e}"
            self.ui.write_log(f"ERR: {error_msg}")
            try:
                self.speak("I encountered a critical error. Please check the logs.")
            except Exception:
                pass
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
        finally:
            try:
                self._chat_sessions.save_current(self._conversation)
                self._active_chat_id = self._chat_sessions.active_id
                self._refresh_chat_list()
            except Exception as e:
                logger.warning("Could not save chat history: %s", e)
