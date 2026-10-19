# web_search.py
# Gemini grounded-search replaced with DuckDuckGo + Ollama LLM summarization.
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _get_base_dir()


def _ddg_search(query: str, max_results: int = 6) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title":   r.get("title",  ""),
                "snippet": r.get("body",   ""),
                "url":     r.get("href",   ""),
            })
    return results


def _usable_results(results: list[dict]) -> list[dict]:
    """Keep only search entries with a link that can be presented as a source."""
    usable = []
    for result in results:
        if not isinstance(result, dict):
            continue
        url = str(result.get("url") or "").strip()
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue
        usable.append({
            "title": str(result.get("title") or url).strip(),
            "snippet": str(result.get("snippet") or "").strip(),
            "url": url,
        })
    return usable


def _format_ddg(query: str, results: list[dict], *, include_urls: bool = True) -> str:
    if not results:
        return f"No web results found for: {query}"
    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}")
        if r.get("snippet"): lines.append(f"   {r['snippet']}")
        if include_urls:
            lines.append(f"   {r['url']}")
        lines.append("")
    return "\n".join(lines).strip()


def _format_sources(results: list[dict]) -> str:
    if not results:
        return ""
    lines = ["Sources:"]
    for i, result in enumerate(results, 1):
        lines.append(f"[{i}] {result['title']} — {result['url']}")
    return "\n".join(lines)


def _append_sources(answer: str, results: list[dict]) -> str:
    sources = _format_sources(results)
    answer = (answer or "").strip()
    if not sources:
        return answer
    return f"{answer}\n\n{sources}" if answer else sources


def _llm_summarize(query: str, raw_results: str) -> str:
    try:
        from core.llm_client import call_llm_text
        system = (
            "You are Aether. Summarize web search results clearly and concisely. "
            "Answer the user's query directly and support factual claims with "
            "the supplied numbered citations, such as [1]. Never invent sources "
            "or citation numbers. Be factual."
        )
        prompt = (
            f"User question: {query}\n\n"
            f"Web search results:\n{raw_results[:4000]}\n\n"
            "Answer the question based on these results and cite the relevant "
            "sources using their supplied numbers:"
        )
        return call_llm_text(prompt, system=system)
    except Exception:
        return raw_results


def _compare(items: list[str], aspect: str) -> str:
    source_results: list[dict] = []
    lines = [f"Comparison — {aspect.upper()}", "─" * 40]
    for item in items:
        try:
            results = _usable_results(_ddg_search(f"{item} {aspect}", max_results=3))[:2]
        except Exception:
            results = []

        lines.append(f"\n▸ {item}")
        for result in results:
            source_results.append(result)
            source_number = len(source_results)
            lines.append(
                f"[{source_number}] {result['title']}\n"
                f"  {result['snippet']}\n"
                f"  {result['url']}"
            )
    raw = "\n".join(lines)
    answer = _llm_summarize(f"Compare {', '.join(items)} regarding {aspect}", raw)
    return _append_sources(answer, source_results)


def web_search(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    query  = params.get("query", "")
    mode   = str(params.get("mode",  "search")).lower().strip()
    items  = params.get("items", [])
    # Small models often pass a list as `query` (e.g. for compare mode).
    # Treat that as items instead of crashing on .strip().
    if isinstance(query, (list, tuple)):
        if not items:
            items = [str(q) for q in query if str(q).strip()]
        query = ""
    query = str(query or "").strip()
    if isinstance(items, str):
        items = [items]
    items = [str(i) for i in (items or []) if str(i).strip()]
    aspect = params.get("aspect", "general").strip() or "general"

    if not query and not items:
        return "Please provide a search query, sir."

    if items and mode != "compare":
        mode = "compare"

    if mode in {"deep", "research", "deep research", "deep_research"}:
        from actions.deep_research import deep_research
        return deep_research(query, player=player)

    if player:
        player.write_log(f"[Search] {query or ', '.join(items)}")

    print(f"[WebSearch] 🔍 Query: {query!r}  Mode: {mode}")

    try:
        if mode == "compare" and items:
            print(f"[WebSearch] 📊 Comparing: {items}")
            return _compare(items, aspect)

        results = _usable_results(_ddg_search(query))
        if not results:
            return f"No web results found for: {query}"
        raw     = _format_ddg(query, results)
        print(f"[WebSearch] ✅ DDG: {len(results)} result(s).")
        # Let Ollama summarise the results for a cleaner spoken response
        answer = _llm_summarize(query, raw)
        return _append_sources(answer, results)

    except Exception as e:
        print(f"[WebSearch] ❌ Failed: {e}")
        return f"Search failed, sir: {e}"
