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
        sys_p   = _load_system_prompt()
        memory  = load_memory()
        mem_str = format_memory_for_prompt(memory)
        now     = datetime.now()
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {now.strftime('%A, %B %d, %Y — %I:%M %p')}\n"
            f"Use this to calculate exact times for reminders."
        )
        parts = [sys_p]
        model_guidance = model_prompt_guidance(model_name)
        if model_guidance:
            parts.append(model_guidance)
        if mem_str:
            parts.append(mem_str)

        if not light:
            # Inject memory context — only from the active engine
            mem_engine = self._hybrid_mem or self._semantic_mem
            if mem_engine and user_text:
                try:
                    enriched = mem_engine.inject_context(user_text, "")
                    if enriched:
                        parts.append(enriched)
                except Exception:
                    pass

            # inject active goals
            if self._goals:
                goal_ctx = self._goals.format_for_prompt()
                if goal_ctx:
                    parts.append(goal_ctx)

            # inject MCP tool list so the LLM knows what's available
            if self._mcp:
                mcp_tools = self._mcp.list_all_tools()
                if mcp_tools:
                    parts.append("[CONNECTED MCP TOOLS]\n" + "\n".join(f"  {t}" for t in mcp_tools))

            # inject plugin tool list
            if self._plugins:
                plugin_tools = self._plugins.list_tools()
                if plugin_tools:
                    parts.append("[LOADED PLUGINS]\n" + "\n".join(f"  plugin.{t}" for t in plugin_tools))

            # inject active integration list
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
