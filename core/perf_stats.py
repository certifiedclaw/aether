"""core/perf_stats.py - speed, memory and cloud-usage numbers per model (roadmap #47).

record() is called once per model round (from conversation_core) and appends a line to
logs/perf.jsonl (rotated at 2 MB, 2 kept). summarize() turns those lines into per-model
speed stats and per-day cloud token totals; memory_snapshot() asks Ollama what is loaded.
Nothing here stores message text - only model, route and numbers.

Config: perf_log (default true), cloud_daily_token_budget (0 = no warning). The budget is
only a heads-up for free tiers; Aether does not enforce it.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = _ROOT / "logs" / "perf.jsonl"
MAX_BYTES = 2 * 1024 * 1024
KEEP = 2
_lock = threading.Lock()


def _cfg() -> dict:
    try:
        from core.llm_client import _load_config
        return _load_config() or {}
    except Exception:  # noqa: BLE001
        return {}


def route_for(stream_kwargs: dict | None = None) -> str:
    """'cloud' | 'remote' | 'local' for the round that just ran."""
    try:
        from core import llm_client as lc
        override = (stream_kwargs or {}).get("provider_override")
        provider = override or lc.get_llm_provider()
        if provider in ("gemini", "openai_api"):
            return "cloud"
        st = lc.get_llm_backend_status()
        return "remote" if st.get("mode") == "dual" and st.get("active") == "remote" else "local"
    except Exception:  # noqa: BLE001
        return "local"


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


def record(model, route: str, prompt_tokens, output_tokens, eval_ns, elapsed_s,
           path: Path | None = None, ttft_s=None) -> None:
    """Append one round. Never raises."""
    try:
        cfg = _cfg()
        v = cfg.get("perf_log", True)
        if (v.strip().lower() in ("0", "false", "no", "off")) if isinstance(v, str) else (v is False):
            return
        out_t, ev, el, tt = _num(output_tokens), _num(eval_ns), _num(elapsed_s), _num(ttft_s)
        tps = None
        if out_t:
            if ev:
                tps = out_t / (ev / 1e9)
            elif el:
                tps = out_t / el
        entry = {"ts": round(time.time(), 1), "model": str(model or "?")[:80], "route": route,
                 "prompt_tokens": _num(prompt_tokens), "output_tokens": out_t,
                 "elapsed_s": round(el, 2) if el is not None else None,
                 "ttft_s": round(tt, 3) if tt is not None else None,
                 "tps": round(tps, 2) if tps else None}
        path = Path(path) if path else LOG_FILE
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate(path)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except Exception:  # noqa: BLE001 - stats must never break a reply
        pass


def _rotate(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size >= MAX_BYTES:
            for i in range(KEEP, 0, -1):
                src = path.with_name(f"{path.name}.{i - 1}" if i > 1 else path.name)
                dst = path.with_name(f"{path.name}.{i}")
                if src.exists():
                    if dst.exists():
                        dst.unlink()
                    src.rename(dst)
    except OSError:
        pass


def read(since: float | None = None, path: Path | None = None) -> list[dict]:
    """Oldest first."""
    path = Path(path) if path else LOG_FILE
    files = [path.with_name(f"{path.name}.{i}") for i in range(KEEP, 0, -1)] + [path]
    out = []
    for p in files:
        if not p.exists():
            continue
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(e, dict) and (since is None or e.get("ts", 0) >= since):
                        out.append(e)
        except OSError:
            continue
    return out


def _pct(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def summarize(entries: list[dict]) -> dict:
    """Per-model speed stats plus per-day cloud tokens."""
    models: dict[str, dict] = {}
    cloud_by_day: dict[str, float] = {}
    for e in entries:
        key = f"{e.get('model', '?')} ({e.get('route', '?')})"
        m = models.setdefault(key, {"n": 0, "el": [], "tt": [], "tps": [], "prompt": 0.0, "output": 0.0})
        m["n"] += 1
        if e.get("elapsed_s") is not None:
            m["el"].append(e["elapsed_s"])
        if e.get("ttft_s") is not None:
            m["tt"].append(e["ttft_s"])
        if e.get("tps"):
            m["tps"].append(e["tps"])
        m["prompt"] += e.get("prompt_tokens") or 0
        m["output"] += e.get("output_tokens") or 0
        if e.get("route") == "cloud":
            day = time.strftime("%Y-%m-%d", time.localtime(e.get("ts", 0)))
            cloud_by_day[day] = cloud_by_day.get(day, 0) + (e.get("prompt_tokens") or 0) + (e.get("output_tokens") or 0)
    rows = []
    for key, m in models.items():
        el, tt = sorted(m["el"]), sorted(m["tt"])
        rows.append({"model": key, "rounds": m["n"],
                     "p50_s": round(_pct(el, 0.5), 2), "p95_s": round(_pct(el, 0.95), 2),
                     "ttft_p50_s": round(_pct(tt, 0.5), 2) if tt else None,
                     "ttft_p95_s": round(_pct(tt, 0.95), 2) if tt else None,
                     "avg_tps": round(sum(m["tps"]) / len(m["tps"]), 1) if m["tps"] else None,
                     "prompt_tokens": int(m["prompt"]), "output_tokens": int(m["output"])})
    rows.sort(key=lambda r: -r["rounds"])
    return {"models": rows, "cloud_tokens_by_day": dict(sorted(cloud_by_day.items()))}


def budget_warning(summary: dict, cfg: dict | None = None, today: str | None = None) -> str:
    cfg = cfg if cfg is not None else _cfg()
    try:
        budget = float(cfg.get("cloud_daily_token_budget", 0) or 0)
    except (TypeError, ValueError):
        budget = 0
    if budget <= 0:
        return ""
    today = today or time.strftime("%Y-%m-%d")
    used = (summary.get("cloud_tokens_by_day") or {}).get(today, 0)
    if used >= budget:
        return f"Cloud tokens today: {int(used):,} - over your {int(budget):,} budget."
    if used >= 0.8 * budget:
        return f"Cloud tokens today: {int(used):,} of {int(budget):,} (80%+ used)."
    return ""


def memory_snapshot(fetch=None) -> dict:
    """Process memory, host free RAM, and what Ollama has loaded (RAM vs GPU)."""
    snap: dict = {"process_mb": None, "host_free_mb": None, "host_total_mb": None, "loaded": []}
    try:
        import psutil
        snap["process_mb"] = round(psutil.Process(os.getpid()).memory_info().rss / 1048576)
        vm = psutil.virtual_memory()
        snap["host_free_mb"] = round(vm.available / 1048576)
        snap["host_total_mb"] = round(vm.total / 1048576)
    except Exception:  # noqa: BLE001
        pass
    try:
        if fetch is None:
            import requests
            from core.llm_client import get_llm_settings
            url = get_llm_settings()[0]
            fetch = lambda: requests.get(f"{url}/api/ps", timeout=2).json()  # noqa: E731
        for m in (fetch() or {}).get("models", []):
            size, vram = m.get("size") or 0, m.get("size_vram") or 0
            snap["loaded"].append({"model": m.get("name", "?"), "size_mb": round(size / 1048576),
                                   "gpu_mb": round(vram / 1048576)})
    except Exception:  # noqa: BLE001 - Ollama may be down or remote
        pass
    return snap


def full_report(since_hours: float = 24.0) -> dict:
    since = time.time() - since_hours * 3600 if since_hours else None
    summ = summarize(read(since=since))
    return {"hours": since_hours, "summary": summ, "memory": memory_snapshot(),
            "warning": budget_warning(summ)}


def render(rep: dict) -> str:
    s = rep["summary"]
    lines = [f"Performance (last {rep['hours']:g} h)"]
    if not s["models"]:
        lines.append("  no model rounds recorded yet")
    for r in s["models"]:
        tps = f"{r['avg_tps']} tok/s" if r["avg_tps"] else "tok/s n/a"
        first = (f"first token {r['ttft_p50_s']}s (p95 {r['ttft_p95_s']}s), "
                 if r.get("ttft_p50_s") is not None else "")
        lines.append(f"  {r['model']}: {r['rounds']} rounds, {first}round median {r['p50_s']}s, p95 {r['p95_s']}s, {tps}, "
                     f"{r['prompt_tokens']:,} in / {r['output_tokens']:,} out tokens")
    if s["cloud_tokens_by_day"]:
        lines.append("  cloud tokens/day: " + ", ".join(f"{d} {int(t):,}" for d, t in s["cloud_tokens_by_day"].items()))
    if rep.get("warning"):
        lines.append("  ! " + rep["warning"])
    m = rep["memory"]
    if m["process_mb"] is not None:
        lines.append(f"  Aether process {m['process_mb']} MB; host free {m['host_free_mb']} of {m['host_total_mb']} MB")
    for x in m["loaded"]:
        where = f"{x['gpu_mb']} MB on GPU" if x["gpu_mb"] else "CPU/RAM"
        lines.append(f"  loaded in Ollama: {x['model']} {x['size_mb']} MB ({where})")
    return "\n".join(lines)
