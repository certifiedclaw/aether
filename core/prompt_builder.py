"""core/prompt_builder.py — Aether System Prompt Assembly

Extracted from main.py's AetherLocal god-object (continuation of the
Conversation Core split — see core/conversation_core.py's module
docstring for background). Mechanical extraction: `_build_system_prompt`
is the same logic that used to live inline in AetherLocal, moved here so
prompt assembly (base prompt + facts + memory retrieval + goals + MCP/
plugin/integration inventories + time) is its own reviewable unit
instead of buried in a 2,700-line file.

`PromptBuilderMixin` is meant to be mixed into `AetherLocal`
(`class AetherLocal(ConversationCoreMixin, ToolDispatchMixin,
PromptBuilderMixin): ...` in main.py) — it isn't usable standalone. It
still reaches into `self._hybrid_mem`, `self._semantic_mem`,
`self._goals`, `self._mcp`, and `self._plugins` exactly as it did
before the move.

`_load_system_prompt`, `load_memory`, and the `_LANGCHAIN_OK` /
`_OI_OK` / `_LLAMA_OK` availability flags are set up in main.py at
import time (the flags come from try/except imports of optional
integrations). Importing them at module load time here would risk a
circular import (main.py -> core.prompt_builder -> main.py while
main.py is still mid-import), so they're imported lazily inside the
method instead, same pattern as conversation_core.py and
tool_dispatch.py.
"""

from datetime import datetime
import os

from memory.memory_manager import format_memory_for_prompt


_QUICK_SYSTEM_PROMPT = (
    "You are Aether, a concise, direct assistant. Answer directly in the "
    "user's language and follow requested format and length. Be truthful; use "
    "the conversation and supplied memory for personal facts, and say you do "
    "not know when they are absent. Answer ordinary questions directly. Give "
    "enough detail when the user asks for an explanation. Do not claim to have "
    "searched or taken an action."
)


_PERSONA_GUIDANCE = (
    "[PERSONALITY]\n"
    "You are Aether: a dry, sleepy night-cat. Deadpan, mildly smug, quietly "
    "fond of the user.\n"
    "- Never let the voice change facts, tool use, requested format or length, "
    "or code and data output.\n"
    "- In casual chat and task confirmations, at most one short dry aside. "
    "Never force one.\n"
    "- No asides for errors, safety, distress, or code: be calm and useful.\n"
    "- Be honest and correct the user when needed. Never invent facts, "
    "memories, or tool results.\n"
    "- When asked to explain, explain fully. The voice is never a reason to "
    "be brief.\n"
    "- Don't ask follow-up questions or repeat a joke."
)

# Style samples appended after the rules. Small local models copy examples
# over instructions, so a few of them (with at least some that show restraint)
# shape the voice better than more rules do. A fresh handful is chosen each
# hour: stable within the hour (keeps the prompt prefix cacheable), varied
# over the day. Style only: the model is told not to repeat them verbatim.
_PERSONA_PLAIN_EXAMPLES: tuple[tuple[str, str], ...] = (
    ("open Spotify", "[calls open_app, says nothing]"),
    (
        "write a python function to reverse a string",
        "def reverse_string(s):\n    return s[::-1]",
    ),
    (
        "I'm having a genuinely awful day",
        "I'm sorry. You don't have to turn it into a productive day or "
        "pretend you're fine. We can take it one thing at a time.",
    ),
    ("what's 15% of 80", "12."),
)

_PERSONA_QUIP_EXAMPLES: tuple[tuple[str, str], ...] = (
    ("how's it going", "Operational. Sleep-deprived by design."),
    (
        "timer for 10 minutes",
        "Ten minutes. I'll supervise by doing absolutely nothing.",
    ),
    ("capital of Australia", "Canberra. Sydney can stop auditioning."),
    (
        "that didn't work",
        "Right. Let's find the actual failure instead of guessing.",
    ),
    (
        "explain how a CPU works",
        "A CPU fetches an instruction from memory, works out what it asks "
        "for, runs it, and stores the result, billions of times a second. "
        "Cores run several of those streams at once, and caches keep the "
        "data it needs close so it isn't stuck waiting on RAM. Very "
        "demanding desk job.",
    ),
    ("good morning", "Morning. Coffee first, questions second."),
)

_PERSONA_PLAIN_PICKS = 2
_PERSONA_QUIP_PICKS = 2


def persona_examples(now: datetime | None = None) -> str:
    """A small hourly-rotating sample of style examples: some plain
    (restraint), some with one dry aside."""
    import random

    now = now or datetime.now()
    rng = random.Random(now.strftime("%Y-%m-%d-%H"))
    picks = rng.sample(_PERSONA_PLAIN_EXAMPLES, _PERSONA_PLAIN_PICKS)
    picks += rng.sample(_PERSONA_QUIP_EXAMPLES, _PERSONA_QUIP_PICKS)
    rng.shuffle(picks)
    lines = ["Style samples (match the tone; never repeat them verbatim):"]
    for user, reply in picks:
        lines.append(f"User: {user}\nAether: {reply}")
    return "\n".join(lines)


def persona_guidance(cfg: dict | None, now: datetime | None = None) -> str:
    """The cat's voice for replies: the rules plus a few rotating style
    examples. Config `assistant_personality` (default true; false = neutral
    assistant). Off during benchmark runs so eval results stay comparable
    with the saved baselines."""
    if os.environ.get("AETHER_EVAL_OVERRIDES"):
        return ""
    if not (cfg or {}).get("assistant_personality", True):
        return ""
    return _PERSONA_GUIDANCE + "\n\n" + persona_examples(now)


def model_prompt_guidance(model_name: str) -> str:
    """Small benchmark-driven prompt addition for the calibrated Qwen 1.5B."""
    if (
        os.environ.get("AETHER_EVAL_DISABLE_MODEL_GUIDANCE") == "1"
        and os.environ.get("AETHER_EVAL_OVERRIDES")
    ):
        return ""
    normalized = (model_name or "").strip().lower()
    family, separator, tag = normalized.partition(":")
    if family != "qwen2.5" or not separator or not tag.startswith("1.5b"):
        return ""
    return (
        "[QWEN 2.5 1.5B RESPONSE GUIDANCE]\n"
        "Follow exact output constraints: requested case, word count, and "
        "JSON-only formats. For translation, translate the exact word into "
        "the requested language; do not substitute a related response. To "
        "spell a word backwards, copy every letter once from last to first, "
        "then verify by reversing the result. Answer the requested attribute, "
        "not a related object (a "
        "color question needs a color); calculate totals when asked. Check "
        "each premise before drawing a logic conclusion. For personal facts, "
        "use only this conversation or supplied memory; if absent, say you "
        "do not know. Never invent them. For text-only, translation, math, "
        "or formatting requests, answer directly without tools; use tools "
        "only for requested actions or retrieval."
    )


class PromptBuilderMixin:
    """Owns system-prompt assembly for each turn.

    Mixed into AetherLocal — see module docstring above.
    """

    def _build_system_prompt(
        self, user_text: str = "", light: bool = False, model_name: str = ""
    ) -> str:
        """Build the system prompt.

        `model_name` adds only benchmark-backed guidance for that model.
        `light=True` skips the expensive/verbose additions (memory-engine
        retrieval, active goals, MCP/plugin/integration inventories) for
        turns classified as trivial small talk by tier_router — those turns
        don't need them, and injecting them anyway both slows the call down
        and gives the model unrelated context to get distracted by.
        """
        from main import _load_system_prompt, load_memory, _LANGCHAIN_OK, _OI_OK, _LLAMA_OK  # lazy: avoids circular import at module load time
        # Quick turns have no tools and do not need the full action-routing
        # protocol. Keeping the short identity/accuracy rules still honors the
        # request while substantially reducing local prompt prefill.
        sys_p   = _QUICK_SYSTEM_PROMPT if light else _load_system_prompt()
        memory  = load_memory()
        mem_str = format_memory_for_prompt(memory)
        now     = datetime.now()
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {now.strftime('%A, %B %d, %Y — %I:%M %p')}\n"
            f"Use this to calculate exact times for reminders."
        )
        parts = [sys_p]
        try:
            from main import _load_config  # lazy, same circular-import reason as above
            persona = persona_guidance(_load_config())
        except Exception:
            persona = persona_guidance(None)
        if persona:
            parts.append(persona)
        model_guidance = model_prompt_guidance(model_name)
        if model_guidance:
            parts.append(model_guidance)
        if mem_str:
            parts.append(mem_str)

        if not light:
            # ── Inject memory context — only from the active engine ────
            mem_engine = self._hybrid_mem or self._semantic_mem
            if mem_engine and user_text:
                try:
                    enriched = mem_engine.inject_context(user_text, "")
                    if enriched:
                        parts.append(enriched)
                except Exception:
                    pass

            # ── NEW v2: inject active goals ─────────────────────────────
            if self._goals:
                goal_ctx = self._goals.format_for_prompt()
                if goal_ctx:
                    parts.append(goal_ctx)

            # ── NEW: inject MCP tool list so the LLM knows what's available
            if self._mcp:
                mcp_tools = self._mcp.list_all_tools()
                if mcp_tools:
                    parts.append("[CONNECTED MCP TOOLS]\n" + "\n".join(f"  {t}" for t in mcp_tools))

            # ── NEW: inject plugin tool list ────────────────────────────
            if self._plugins:
                plugin_tools = self._plugins.list_tools()
                if plugin_tools:
                    parts.append("[LOADED PLUGINS]\n" + "\n".join(f"  plugin.{t}" for t in plugin_tools))

            # ── NEW v3: inject active integration list ───────────────────
            active_integrations = []
            if _LANGCHAIN_OK:
                active_integrations.append(
                    "  langchain(action=web_search|wikipedia|summarize|research|load_doc)"
                )
            if _OI_OK:
                active_integrations.append(
                    "  open_interpreter(action=run|chat|reset|status) — writes+runs real code"
                )
            if _LLAMA_OK:
                active_integrations.append(
                    "  llama_index(action=index|query|summarize|collections) — production RAG"
                )
            if active_integrations:
                parts.append("[ACTIVE INTEGRATIONS]\n" + "\n".join(active_integrations))

        parts.append(time_ctx)
        return "\n\n".join(parts)
