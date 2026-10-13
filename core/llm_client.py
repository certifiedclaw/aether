"""
Local LLM client for Aether.

Supports local Ollama and OpenAI-compatible hosted/local backends — selected
via "llm_provider" in config/api_keys.json:

  "llm_provider": "ollama"   (default)
        Uses Ollama's native /api/chat endpoint.
        Download: https://ollama.com
        Default port: 11434

  "llm_provider": "openai"  (LM Studio, Jan, LocalAI, etc.)
        Uses any OpenAI-compatible server: LM Studio, Jan, LocalAI,
        llama.cpp server, vLLM, etc.
        LM Studio download: https://lmstudio.ai   (default port: 1234)
        Set  "llm_url": "http://localhost:1234"  in config.
        Note: tool-calling support depends on the model; use a model that
        supports function/tool calls (e.g. Qwen2.5, Llama-3.1, Mistral).

  "llm_provider": "gemini"  (Google Gemini)
        Uses Gemini's OpenAI-compatibility endpoint (no extra SDK needed).
        Get API key from: https://aistudio.google.com/app/apikey
        Set  "llm_url": "https://generativelanguage.googleapis.com/v1beta/openai"  in config.
        Set  "gemini_api_key": "your-api-key-here"  in config/api_keys.json.
        Sent as an  Authorization: Bearer <key>  header on every request.

  "llm_provider": "openai_api"  (OpenAI API)
        Uses https://api.openai.com/v1/chat/completions with the configured
        model ID and openai_api_key. API usage is billed separately from ChatGPT.
"""
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Generator

import logging
import requests

logger = logging.getLogger("aether")

# Matches a sentence boundary: [.!?] followed by whitespace, or a blank line.
# Avoids splitting on decimals (3.5) because those have no space after the dot.
_SENT_END = re.compile(r'(?<=[.!?])\s+|(?<=\n)\s*\n')

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR    = get_base_dir()
CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

_DEFAULTS = {
    "llm_url":      "http://localhost:11434",
    "llm_model":    "llama3.2",
    "llm_provider": "ollama",   # "ollama" | "openai" | "gemini"
}


def _auth_headers(provider: str, cfg: dict | None = None) -> dict:
    """Bearer-auth header for hosted providers; local backends stay unauthenticated.
    """
    if provider == "gemini":
        secret_name = "gemini_api_key"
    elif provider == "openai_api":
        secret_name = "openai_api_key"
    else:
        return {}
    cfg = cfg if cfg is not None else _load_config()
    from core.credentials import get_secret
    key = get_secret(secret_name, cfg)
    return {"Authorization": f"Bearer {key}"} if key else {}


def get_gemini_settings() -> tuple[str, str]:
    """Returns (gemini_api_key, gemini_model) from config — independent of
    whatever "llm_provider" is currently set to. Used by tier_router's
    HARD-tier escalation, which can fire even when the primary configured
    provider is Ollama. gemini_model falls back to "gemini_escalation_model"
    then a sane default if unset.
    """
    cfg = _load_config()
    from core.credentials import get_secret
    key   = get_secret("gemini_api_key", cfg)      # env > OS keyring > config file
    model = cfg.get("gemini_escalation_model") or cfg.get("gemini_model") or "gemini-3.8-flash"
    return key, model


GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


def _chat_completions_endpoint(url: str, provider: str) -> str:
    """Build the chat-completions URL for an OpenAI-compatible backend.

    Gemini's OpenAI-compat base URL already ends in ".../v1beta/openai" --
    its actual route is just "/chat/completions" with no extra "/v1"
    segment. Appending "/v1/chat/completions" (correct for LM Studio,
    LocalAI, and other genuine OpenAI-compatible servers, which do expect
    that prefix) produces ".../v1beta/openai/v1/chat/completions" for
    Gemini specifically -- a 404, since that path doesn't exist. Every
    call site building this URL must go through here instead of
    hardcoding the suffix, or this regresses the next time someone adds
    a fourth call site.
    """
    if provider == "gemini":
        return f"{url}/chat/completions"
    return f"{url}/v1/chat/completions"


def _is_tool_tag_prefix(text: str) -> bool:
    """True if `text` starts with (or is still a partial prefix of) a raw
    <tool_call> tag, so streamed sentences should be held back, not spoken."""
    t = text.lstrip()
    if not t:
        return False
    tag = "<tool_call>"
    return t.startswith(tag) or (len(t) < len(tag) and tag.startswith(t))


def _num_ctx() -> int:
    """Context window Aether asks Ollama to run with (config key "llm.num_ctx").

    Ollama does NOT use the model's maximum context: it runs at its own default
    (often 4096) unless `options.num_ctx` is sent. Aether's tool schemas alone can
    exceed 6000 tokens, so without this the prompt is silently truncated and the
    model loses its instructions/tools. Every Ollama request must send the SAME
    value -- a different num_ctx makes Ollama reload the model.
    """
    try:
        v = int(_load_config().get("llm.num_ctx", 8192))
    except (TypeError, ValueError):
        v = 8192
    return max(2048, min(v, 131072))


def get_config_flag(name: str, default: bool = False) -> bool:
    """Boolean config flag from config/api_keys.json (accepts true/1/yes/on)."""
    v = _load_config().get(name, default)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def get_llm_provider() -> str:
    """Returns the configured local or hosted provider."""
    raw = _load_config().get("llm_provider", "ollama").strip().lower()
    if raw == "ollama":
        return "ollama"
    elif raw == "gemini":
        return "gemini"
    elif raw == "openai_api":
        return "openai_api"
    else:
        # Default to openai for LM Studio, LocalAI, Jan, etc.
        return "openai"


_config_cache: dict | None = None
_config_mtime: float | None = None


def _ollama_missing_model_message(model: str, url: str) -> str:
    """Actionable text for an Ollama 404: which model, what is installed, how to fix it.

    A bare name like 'llama3.2' means 'llama3.2:latest' to Ollama, so having only
    'llama3.2:3b' pulled is a 404 -- the case that scrambled a whole verify run."""
    msg = f"Model '{model}' not found in Ollama."
    try:
        r = requests.get(f"{url}/api/tags", timeout=3)
        installed = [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:  # noqa: BLE001 - the hint is best-effort
        installed = []
    if installed:
        base = model.split(":")[0]
        close = [n for n in installed if n.split(":")[0] == base]
        if close:
            msg += f" Installed variants: {', '.join(close)} -- set llm_model to one of them in config/api_keys.json."
        else:
            msg += f" Installed: {', '.join(installed)}."
    return msg + f" Or run: ollama pull {model}"


def _load_config() -> dict:
    """Reads config/api_keys.json, cached and invalidated on file change.

    Avoids hitting disk on every LLM call (get_llm_provider/get_llm_settings
    are each called multiple times per turn).
    """
    global _config_cache, _config_mtime
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except Exception:
        return _config_cache or {}

    if _config_cache is None or mtime != _config_mtime:
        try:
            _config_cache = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            _config_cache = {}
        _config_mtime = mtime

    return _config_cache


def _openai_safe_messages(messages: list) -> list:
    """Convert Ollama-style history to the strict OpenAI/Gemini shape.

    Ollama stores assistant tool_calls with `arguments` as a dict and often no
    id/type; Gemini's OpenAI endpoint rejects that with
    400 "Value is not a string". Returns a copy; the input is left untouched
    (the same history is still sent to Ollama in its native form)."""
    import copy
    out = copy.deepcopy(messages)
    pending: list[str] = []          # tool_call ids awaiting a matching tool message
    n = 0
    for m in out:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            pending = []
            fixed = []
            for tc in m["tool_calls"]:
                fn = dict(tc.get("function") or {})
                args = fn.get("arguments", "")
                if not isinstance(args, str):
                    args = json.dumps(args if args is not None else {}, ensure_ascii=False)
                fn["arguments"] = args or "{}"
                tc_id = tc.get("id") or f"call_{n}"
                n += 1
                pending.append(tc_id)
                fixed.append({"id": tc_id, "type": "function", "function": fn})
            m["tool_calls"] = fixed
            if m.get("content") is None:
                m["content"] = ""
        elif role == "tool":
            if not m.get("tool_call_id") and pending:
                m["tool_call_id"] = pending.pop(0)
            elif m.get("tool_call_id") in pending:
                pending.remove(m["tool_call_id"])
            if not isinstance(m.get("content"), str):
                m["content"] = str(m.get("content", ""))
    return out


def ensure_ollama_running(timeout: int = 15) -> bool:
    """
    For Ollama: ping /api/tags; auto-launch 'ollama serve' if not running.
    For OpenAI-compatible providers: just ping /v1/models (server must be started manually).
    Returns True if the LLM server is reachable.
    """
    url, _   = get_llm_settings()
    provider = get_llm_provider()

    if provider != "ollama":
        # OpenAI-compatible servers (LM Studio, LocalAI, Gemini, etc.) must be
        # started/reachable already — we just check if they're reachable.
        # Gemini's OpenAI-compat base already ends in /v1beta/openai; its model
        # list is /models there, and "/v1/models" would 404 and look "down".
        health = f"{url}/models" if provider == "gemini" else f"{url}/v1/models"
        try:
            ok = requests.get(health, headers=_auth_headers(provider), timeout=5).status_code == 200
            if ok:
                print(f"[LLM] OpenAI-compatible server reachable at {url}")
            else:
                print(f"[LLM] Server at {url} returned non-200.  Is it running?")
            return ok
        except Exception as e:
            print(
                f"[LLM] Cannot reach OpenAI-compatible server at {url}: {e}\n"
                "      Make sure LM Studio / LocalAI / Jan is running and the server is started."
            )
            return False

    # ── Ollama ──────────────────────────────────────────────────────────────
    health = f"{url}/api/tags"

    def _is_up() -> bool:
        try:
            return requests.get(health, timeout=3).status_code == 200
        except Exception:
            return False

    if _is_up():
        return True

    print("[LLM] Ollama not running — launching 'ollama serve'…")
    try:
        kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen(["ollama", "serve"], **kwargs)
    except FileNotFoundError:
        print("[LLM] 'ollama' command not found. Install Ollama from https://ollama.com")
        return False
    except Exception as e:
        print(f"[LLM] Could not launch Ollama: {e}")
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1.0)
        if _is_up():
            print("[LLM] Ollama started successfully.")
            return True

    print("[LLM] Ollama did not respond within the timeout.")
    return False


def warmup_model(system_prompt: str | None = None) -> bool:
    """
    Pre-load the model AND prime Ollama's KV prefix cache.

    Why the system_prompt matters
    ─────────────────────────────
    Ollama caches the KV attention state of the prompt prefix across requests.
    If warmup includes the same system prompt that real requests will use, Ollama
    evaluates those tokens ONCE at startup.  Every subsequent request only needs
    to evaluate the small delta (user message ± time context) instead of the full
    300-500 token system prompt → drops first-token latency from ~17 s to <1 s.

    Pass the *static* part of the system prompt (the Aether protocol text, without
    timestamps or per-minute context) so the prefix stays valid across calls.
    """
    url, model = get_llm_settings()
    provider   = get_llm_provider()
    print(f"[LLM] Warming up '{model}' ({provider})…")

    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": "hi"})

    if provider != "ollama":
        # OpenAI-compatible: just fire a minimal request to ensure the model is loaded.
        # No keep_alive or KV-cache priming available — server manages this internally.
        token_param = "max_completion_tokens" if provider == "openai_api" else "max_tokens"
        payload = {
            "model":      model,
            "messages":   messages,
            "stream":     False,
            token_param: 1,
        }
        try:
            resp = requests.post(_chat_completions_endpoint(url, provider), json=payload,
                                  headers=_auth_headers(provider), timeout=180)
            resp.raise_for_status()
            print(f"[LLM] '{model}' ready ({provider}).")
            return True
        except Exception as e:
            print(f"[LLM] Warmup failed (non-fatal): {e}")
            return False

    # ── Ollama ──────────────────────────────────────────────────────────────
    payload = {
        "model":      model,
        "messages":   messages,
        "stream":     False,
        "keep_alive": -1,
        "think":      False,  # reasoning models (qwen3, deepseek-r1, …) skip <think> — see call_llm
        # num_gpu:99 → push ALL transformer layers to GPU (Ollama caps at available)
        # This is safe even without a GPU — Ollama silently ignores if n_gpu_layers=0
        "options":    {"num_predict": 1, "num_gpu": 99, "num_ctx": _num_ctx()},
    }
    try:
        resp = requests.post(f"{url}/api/chat", json=payload, timeout=180)
        resp.raise_for_status()
        print(f"[LLM] '{model}' loaded and KV cache primed.")
        return True
    except Exception as e:
        print(f"[LLM] Warmup failed (non-fatal): {e}")
        return False


_DEFAULT_HEALTH_CHECK_INTERVAL = 20.0   # seconds a reachable/unreachable verdict is cached
_DEFAULT_HEALTH_CHECK_TIMEOUT  = 2.5    # seconds to wait for the remote host to answer

_endpoint_lock  = threading.Lock()
_endpoint_cache = {"checked_at": 0.0, "using": None}   # using: "remote" | "local" | None


def _probe_ollama(url: str, timeout: float) -> bool:
    try:
        return requests.get(f"{url}/api/tags", timeout=timeout).status_code == 200
    except Exception:
        return False


def get_llm_settings() -> tuple[str, str]:
    """Returns (base_url, model_name).

    Single-host mode (default, unchanged behavior): reads "llm_url" /
    "llm_model" from config, no network calls.

    Dual-host mode: activates only when "llm_remote_url" is set in
    config/api_keys.json -- e.g. your home PC's Ollama, for GPU
    inference reached over LAN/Tailscale. When active, this prefers the
    remote host and health-checks it (GET /api/tags) on a short, cached
    interval ("llm_health_check_interval", default 20s) so most calls
    pay no network cost. If the remote host doesn't answer within
    "llm_health_check_timeout" (default 2.5s) -- e.g. the PC is off or
    asleep -- this falls back to "llm_local_url" (default
    http://localhost:11434, i.e. CPU inference on this machine) until
    the remote host is reachable again. Optional "llm_remote_model" /
    "llm_local_model" let you run a bigger model remotely and a smaller,
    CPU-friendly one locally; both default to "llm_model" if unset.

    ensure_ollama_running() is called downstream with whatever URL this
    resolves to, so if the remote host is down AND the local fallback
    Ollama isn't running yet, it gets auto-launched -- "goes back to
    CPU" actually starts CPU inference, not just points at a dead URL.
    """
    cfg        = _load_config()
    remote_url = (cfg.get("llm_remote_url") or "").strip().rstrip("/")

    if not remote_url:
        # Unchanged single-host behavior -- no remote configured.
        url   = cfg.get("llm_url",   _DEFAULTS["llm_url"]).rstrip("/")
        model = cfg.get("llm_model", _DEFAULTS["llm_model"])
        return url, model

    remote_model = cfg.get("llm_remote_model") or cfg.get("llm_model", _DEFAULTS["llm_model"])
    local_url    = (cfg.get("llm_local_url") or _DEFAULTS["llm_url"]).strip().rstrip("/")
    local_model  = cfg.get("llm_local_model") or cfg.get("llm_model", _DEFAULTS["llm_model"])
    interval     = float(cfg.get("llm_health_check_interval", _DEFAULT_HEALTH_CHECK_INTERVAL))
    probe_timeout = float(cfg.get("llm_health_check_timeout", _DEFAULT_HEALTH_CHECK_TIMEOUT))

    now = time.time()
    with _endpoint_lock:
        cached = _endpoint_cache["using"]
        stale  = (now - _endpoint_cache["checked_at"]) >= interval
        if cached is not None and not stale:
            return (remote_url, remote_model) if cached == "remote" else (local_url, local_model)

    # Cache empty or stale -- probe the remote host. Short timeout so an
    # off/asleep PC doesn't stall this call for long.
    reachable = _probe_ollama(remote_url, probe_timeout)
    with _endpoint_lock:
        prev = _endpoint_cache["using"]
        _endpoint_cache["checked_at"] = now
        _endpoint_cache["using"] = "remote" if reachable else "local"
        changed = prev != _endpoint_cache["using"]

    if changed:
        if reachable:
            print(f"[LLM] Remote host reachable ({remote_url}) -- using it for inference.")
        else:
            print(f"[LLM] Remote host unreachable ({remote_url}) -- falling back to local CPU ({local_url}).")

    return (remote_url, remote_model) if reachable else (local_url, local_model)


def get_llm_backend_status() -> dict:
    """Returns a snapshot of the current LLM backend for diagnostics/dashboard
    display: {"mode": "single"|"dual", "active": "remote"|"local"|None,
    "url": <current base_url>, "model": <current model>,
    "remote_url": <configured remote or None>, "local_url": <fallback url>}.
    Does not itself trigger a health check -- reports whatever
    get_llm_settings() last resolved (or resolves now, if dual-mode and
    nothing's been resolved yet this process)."""
    cfg        = _load_config()
    remote_url = (cfg.get("llm_remote_url") or "").strip().rstrip("/")
    url, model = get_llm_settings()

    if not remote_url:
        return {"mode": "single", "active": None, "url": url, "model": model,
                "remote_url": None, "local_url": None}

    with _endpoint_lock:
        active = _endpoint_cache["using"]
    local_url = (cfg.get("llm_local_url") or _DEFAULTS["llm_url"]).strip().rstrip("/")
    return {"mode": "dual", "active": active, "url": url, "model": model,
            "remote_url": remote_url, "local_url": local_url}


_DEFAULT_CONTEXT_WINDOW = 4096
_context_window_cache: dict[str, int] = {}


def get_context_window(model: str | None = None) -> int:
    """Returns the context window (in tokens) for the given model.

    For Ollama, queries /api/show and reads the "<arch>.context_length"
    field from model_info. Result is cached per model name so this is
    only a network call on the first lookup. Falls back to a sane
    default for OpenAI-compatible providers or on any failure.
    """
    url, default_model = get_llm_settings()
    model = model or default_model

    if model in _context_window_cache:
        return _context_window_cache[model]

    ctx = _DEFAULT_CONTEXT_WINDOW
    if get_llm_provider() == "ollama":
        try:
            resp = requests.post(f"{url}/api/show", json={"model": model}, timeout=5)
            resp.raise_for_status()
            model_info = resp.json().get("model_info", {})
            for key, val in model_info.items():
                if key.endswith(".context_length") and val:
                    ctx = int(val)
                    break
            ctx = min(ctx, _num_ctx())   # what Ollama actually runs with, not the model maximum
        except Exception:
            pass

    _context_window_cache[model] = ctx
    return ctx


def call_llm(
    messages: list,
    tools:    list | None = None,
    timeout:  int = 120,
) -> dict:
    """
    Non-streaming chat request.  Routes to Ollama or OpenAI-compatible backend.

    Returns:
        {"content": str, "tool_calls": list}
    """
    url, model = get_llm_settings()
    provider   = get_llm_provider()

    if provider == "ollama":
        endpoint = f"{url}/api/chat"
        payload = {
            "model":      model,
            "messages":   messages,
            "stream":     False,
            "keep_alive": -1,
            "think":      False,  # reasoning models (qwen3, deepseek-r1, …) otherwise burn
                                   # the whole num_predict budget on a hidden <think> block
                                   # and never reach the actual answer/tool call.
            "options":    {"num_predict": 150, "num_gpu": 99, "num_ctx": _num_ctx()},
        }
        if tools:
            payload["tools"] = tools

        try:
            resp = requests.post(endpoint, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            msg  = data.get("message", {})
            return {
                "content":    (msg.get("content") or "").strip(),
                "tool_calls": msg.get("tool_calls") or [],
            }
        except requests.exceptions.ConnectionError as e:
            print(f"[LLM] ConnectionError — trying to restart Ollama… ({e})")
            if ensure_ollama_running():
                try:
                    resp = requests.post(endpoint, json=payload, timeout=timeout)
                    resp.raise_for_status()
                    data = resp.json()
                    msg  = data.get("message", {})
                    return {
                        "content":    (msg.get("content") or "").strip(),
                        "tool_calls": msg.get("tool_calls") or [],
                    }
                except Exception:
                    pass
            raise RuntimeError(
                f"Cannot connect to Ollama at {url}. "
                "Make sure Ollama is installed and run: ollama serve"
            )
        except requests.exceptions.Timeout:
            raise RuntimeError("Ollama request timed out after 120 s.")
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code
            body   = e.response.text[:400]
            print(f"[LLM] HTTPError: {status} — {body}")
            if status == 404 or "model not found" in body.lower() or "pull" in body.lower():
                raise RuntimeError(_ollama_missing_model_message(model, url))
            raise RuntimeError(f"Ollama HTTP error: {status}")
        except Exception as e:
            print(f"[LLM] Unexpected error: {type(e).__name__}: {e}")
            raise RuntimeError(f"LLM call failed: {e}")

    # ── OpenAI-compatible ───────────────────────────────────────────────────
    endpoint = _chat_completions_endpoint(url, provider)
    payload: dict = {
        "model": model,
        "messages": _openai_safe_messages(messages),
    }

    if provider in ("openai", "openai_api"):
        payload["stream"]     = False
        if provider == "openai_api":
            payload["max_completion_tokens"] = _cloud_max_tokens(provider)
        else:
            payload["max_tokens"] = 150
    if provider in ("gemini", "openai_api") and tools:
        payload["tools"]       = tools
        payload["tool_choice"] = "auto"

    try:
        resp = requests.post(endpoint, json=payload, headers=_auth_headers(provider), timeout=timeout)
        resp.raise_for_status()
        choice = resp.json().get("choices", [{}])[0]
        msg    = choice.get("message", {})
        # OpenAI tool_calls format → normalise to Ollama-style
        raw_tc  = msg.get("tool_calls") or []
        tc_list = [
            {
                "id":       t.get("id", ""),
                "function": {
                    "name":      t["function"]["name"],
                    "arguments": (
                        json.loads(t["function"]["arguments"])
                        if isinstance(t["function"].get("arguments"), str)
                        else t["function"].get("arguments", {})
                    ),
                },
            }
            for t in raw_tc
        ]
        return {
            "content":    (msg.get("content") or "").strip(),
            "tool_calls": tc_list,
        }
    except Exception as e:
        raise RuntimeError(f"OpenAI-compatible LLM call failed: {e}")


def call_llm_text(
    prompt:  str,
    system:  str | None = None,
    model:   str | None = None,
    timeout: int = 120,
    tier:    str = "default",
    url:     str | None = None,
    *,
    provider_override: str | None = None,
    model_override:    str | None = None,
) -> str:
    """
    Simple text-only generation (no tools).
    Used by planner, executor, error_handler, code_helper, dev_agent.

    tier: "fast"    — short timeout, lower num_predict (classification, error analysis)
          "deep"    — longer timeout, higher num_predict (code generation, planning)
          "default" — standard settings (everything else)

    provider_override / model_override: route just this one call to a
    different backend than the configured default (e.g. tier_router's
    Gemini escalation) without touching saved config.
    """
    _default_url, _default_model = get_llm_settings()
    url      = url or _default_url
    m        = model_override or model or _default_model
    provider = provider_override or get_llm_provider()

    # Tier-specific overrides
    if tier == "fast":
        timeout = min(timeout, 30)
        num_predict = 300
    elif tier == "deep":
        timeout = max(timeout, 180)
        num_predict = 1200
    else:
        num_predict = 600

    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    if provider != "ollama":
        # OpenAI-compatible backend (LM Studio, LocalAI, Gemini's OpenAI-compat
        # endpoint, …). No keep_alive/num_predict options here — max_tokens is
        # the closest equivalent and only some servers honor it.
        endpoint = _chat_completions_endpoint(url, provider)
        token_param = "max_completion_tokens" if provider == "openai_api" else "max_tokens"
        payload  = {"model": m, "messages": _openai_safe_messages(messages), "stream": False, token_param: num_predict}
        try:
            resp = requests.post(endpoint, json=payload, headers=_auth_headers(provider), timeout=timeout)
            resp.raise_for_status()
            choice = resp.json().get("choices", [{}])[0]
            return (choice.get("message", {}).get("content") or "").strip()
        except Exception as e:
            raise RuntimeError(f"LLM text call failed ({provider}): {e}")

    endpoint = f"{url}/api/chat"
    payload = {"model": m, "messages": messages, "stream": False, "keep_alive": -1, "think": False, "options": {"num_predict": num_predict, "num_ctx": _num_ctx()}}

    try:
        resp = requests.post(endpoint, json=payload, timeout=timeout)
        resp.raise_for_status()
        return (resp.json().get("message", {}).get("content") or "").strip()
    except requests.exceptions.ConnectionError:
        if ensure_ollama_running():
            try:
                resp = requests.post(endpoint, json=payload, timeout=timeout)
                resp.raise_for_status()
                return (resp.json().get("message", {}).get("content") or "").strip()
            except Exception:
                pass
        raise RuntimeError(
            f"Cannot connect to Ollama at {url}. "
            "Make sure Ollama is installed and run: ollama serve"
        )
    except Exception as e:
        raise RuntimeError(f"LLM text call failed: {e}")


def _stream_openai_once(
    messages: list,
    tools:    list | None,
    timeout:  int,
    *,
    provider_override: str | None = None,
    url_override:      str | None = None,
    model_override:    str | None = None,
    cancel_event=None,
) -> Generator[dict, None, None]:
    """
    Streaming backend for OpenAI-compatible servers (LM Studio, LocalAI,
    Gemini's OpenAI-compat endpoint, Jan…).

    Normally reads url/model from config via get_llm_settings(). Pass
    url_override + model_override together to hit a *different* backend
    for this one call without touching the saved config — e.g. tier_router
    escalating a single turn to Gemini while the configured default
    provider is Ollama.

    Parses Server-Sent Events (SSE) and accumulates streaming tool-call fragments
    so the output format is identical to the Ollama backend.
    """
    if url_override and model_override:
        url, model = url_override, model_override
    else:
        url, model = get_llm_settings()
    provider = provider_override or get_llm_provider()
    endpoint = _chat_completions_endpoint(url, provider)

    payload: dict = {
        "model":      model,
        "messages":   _openai_safe_messages(messages),
        "stream":     True,
        ("max_completion_tokens" if provider == "openai_api" else "max_tokens"):
            _cloud_max_tokens(provider),
    }
    if tools:
        payload["tools"]       = tools
        payload["tool_choice"] = "auto"

    try:
        with requests.post(endpoint, json=payload, headers=_auth_headers(provider),
                            timeout=timeout, stream=True) as resp:
            if resp.status_code >= 400:
                # Read the body now: after the `with` exits the stream is closed and
                # e.response.text would be empty/raise, hiding the real API error.
                try:
                    _err_body = resp.text[:300].replace("\n", " ")
                except Exception:
                    _err_body = ""
                raise RuntimeError(f"OpenAI-compatible HTTP error: {resp.status_code} {_err_body}".strip())
            full_content = ""
            buf          = ""
            _hit_length  = False
            # tool_call fragments: index → {"id", "function": {"name", "arguments"}}
            tc_fragments: dict[int, dict] = {}

            for raw in resp.iter_lines():
                if cancel_event is not None and cancel_event.is_set():
                    yield {"type": "cancelled", "content": full_content}
                    return
                if not raw:
                    continue
                # SSE lines look like: b"data: {...}" or b"data: [DONE]"
                line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue

                choice = chunk.get("choices", [{}])[0]
                delta  = choice.get("delta", {})
                text   = delta.get("content") or ""

                full_content += text
                buf          += text

                # Accumulate sentence boundaries for streaming TTS
                while True:
                    m = _SENT_END.search(buf)
                    if not m:
                        break
                    sentence = buf[: m.start() + 1].strip()
                    buf      = buf[m.end():]
                    if sentence:
                        yield {"type": "sentence", "text": sentence}

                # Accumulate streaming tool-call fragments
                for tc in (delta.get("tool_calls") or []):
                    idx = tc.get("index", 0)
                    if idx not in tc_fragments:
                        tc_fragments[idx] = {"id": "", "function": {"name": "", "arguments": ""}}
                    frag = tc_fragments[idx]
                    frag["id"] = frag["id"] or tc.get("id", "")
                    fn = tc.get("function", {})
                    frag["function"]["name"]      += fn.get("name") or ""
                    frag["function"]["arguments"] += fn.get("arguments") or ""

                finish = choice.get("finish_reason")
                if finish == "length":
                    _hit_length = True
                if finish in ("stop", "tool_calls", "length"):
                    break

            # Flush any trailing content
            if buf.strip():
                yield {"type": "sentence", "text": buf.strip()}

            # Parse accumulated tool-call argument strings → dicts
            tool_calls: list = []
            for idx in sorted(tc_fragments):
                frag = tc_fragments[idx]
                args = frag["function"]["arguments"]
                try:
                    args = json.loads(args)
                except Exception:
                    pass   # leave as raw string; _execute_tool handles it
                tool_calls.append({
                    "id":       frag["id"],
                    "function": {"name": frag["function"]["name"], "arguments": args},
                })

            # OpenAI-compatible servers generally don't report token counts
            # mid-stream; give a rough estimate so the stats panel has
            # something to show (prompt_tokens unavailable here).
            stats = {
                "model":          model,
                "prompt_tokens":  None,
                "output_tokens":  max(1, len(full_content) // 4),
                "eval_duration":  None,
                "total_duration": None,
                "truncated":      _hit_length,
            }

            yield {
                "type":       "done",
                "content":    full_content.strip(),
                "tool_calls": tool_calls,
                "stats":      stats,
            }
            return

    except RuntimeError:
        raise
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Cannot reach OpenAI-compatible server at {url}.\n"
            "Make sure LM Studio / LocalAI / Jan is running and the server is started."
        )
    except requests.exceptions.Timeout:
        raise RuntimeError("OpenAI-compatible stream timed out.")
    except requests.exceptions.HTTPError as e:
        try:
            _body = e.response.text[:300].replace("\n", " ")
        except Exception:
            _body = ""
        raise RuntimeError(f"OpenAI-compatible HTTP error: {e.response.status_code} {_body}".strip())
    except Exception as e:
        raise RuntimeError(f"OpenAI-compatible stream failed: {e}")


def _cloud_max_tokens(provider: str) -> int:
    """Reply budget for OpenAI-compatible calls. Local/other servers keep the
    short voice-reply cap (150). Gemini needs far more: its hidden reasoning
    tokens count against max_tokens, so 150 produced replies cut off after a
    few words ("The single animal mentioned most is"). Config "gemini_max_tokens"."""
    if provider == "openai_api":
        try:
            v = int(_load_config().get("openai_max_tokens", 1200))
        except Exception:
            v = 1200
        return max(256, min(v, 8192))
    if provider != "gemini":
        return 150
    try:
        v = int(_load_config().get("gemini_max_tokens", 2048))
    except Exception:
        v = 2048
    return max(256, min(v, 8192))


# ── Gemini circuit breaker ────────────────────────────────────────────────────
# After a real failure (the single retry also failed) Gemini ESCALATION calls
# fast-fail for a while, so each HARD turn during an outage doesn't pay the
# timeout + retry before falling back to the local model. Keyed per model.
# Only applies to per-turn escalations (provider_override == "gemini"); when
# Gemini is the primary provider it is never blocked by this.
_GEMINI_COOLDOWNS: dict[str, tuple[float, str]] = {}   # model -> (until monotonic, reason)
_COOL_LOCK = threading.Lock()   # escalations run on worker threads
_HTTP_CODE_RE = re.compile(r"HTTP error:\s*(\d{3})\b")


def gemini_cooldown_seconds() -> int:
    """Outage cooldown in seconds (config "gemini_cooldown"); 0 disables the breaker."""
    try:
        v = int(_load_config().get("gemini_cooldown", 120))
    except Exception:
        v = 120
    return max(0, min(v, 3600))


def _cooldown_for_error(msg: str) -> int:
    """Seconds to rest Gemini for this failure; 0 = do not cool down."""
    base = gemini_cooldown_seconds()
    if base <= 0:
        return 0
    m = _HTTP_CODE_RE.search(msg)
    if m:
        code = int(m.group(1))
        if code >= 500:
            return base                    # outage: believed for a while
        if code == 429:
            return max(base, 300)          # quota refills slowly
        if code in (401, 403, 404):
            return max(base, 3600)         # bad key / no access / no such model
        if code == 400 and ("API key not valid" in msg or "API_KEY_INVALID" in msg):
            return max(base, 3600)         # Gemini reports a bad key as 400, not 401
        return 0                           # other 400s may be about THIS request only
    if "timed out" in msg or "Cannot reach" in msg or "stream failed" in msg:
        return base
    return 0


def _cooldown_state(model: str) -> tuple[float, str]:
    """(seconds left, reason), read atomically; (0.0, "") when not resting."""
    with _COOL_LOCK:
        entry = _GEMINI_COOLDOWNS.get(model)
        if not entry:
            return 0.0, ""
        left = entry[0] - time.monotonic()
        if left <= 0:
            _GEMINI_COOLDOWNS.pop(model, None)
            return 0.0, ""
        return left, entry[1]


def gemini_cooldown_remaining(model: str) -> float:
    return _cooldown_state(model)[0]


def reset_gemini_cooldowns() -> None:
    with _COOL_LOCK:
        _GEMINI_COOLDOWNS.clear()


def _note_gemini_failure(model: str, msg: str) -> None:
    secs = _cooldown_for_error(msg)
    if secs > 0:
        with _COOL_LOCK:
            _GEMINI_COOLDOWNS[model] = (time.monotonic() + secs, msg[:100])
        logger.warning("Gemini '%s' resting for %ds after: %s", model, secs, msg[:100])


_TRANSIENT_HTTP_RE = re.compile(r"HTTP error:\s*(500|502|503|504)\b")


def gemini_retry_delay() -> float:
    """Seconds to wait before the single retry of a transient Gemini server error.
    Config "gemini_retry_delay"; 0 disables the retry."""
    try:
        v = float(_load_config().get("gemini_retry_delay", 1.0))
    except Exception:
        v = 1.0
    return max(0.0, min(v, 5.0))


def _stream_openai(
    messages: list,
    tools:    list | None,
    timeout:  int,
    *,
    provider_override: str | None = None,
    url_override:      str | None = None,
    model_override:    str | None = None,
    cancel_event=None,
) -> Generator[dict, None, None]:
    """_stream_openai_once() plus ONE short retry for Gemini on transient 5xx.

    Gemini regularly answers 503 "high demand" for a second or two. Without a
    retry every such blip dropped the turn to the slower local model. The retry
    only happens when the HTTP error arrived before any event was produced, so
    nothing can be spoken twice; timeouts and 4xx (bad key, quota, bad request)
    are not retried and fall back to local immediately, as before.
    """
    provider   = provider_override or get_llm_provider()
    delay      = gemini_retry_delay() if provider == "gemini" else 0.0
    escalation = provider_override == "gemini"
    key        = ""
    if escalation:
        try:
            key = model_override or get_llm_settings()[1] or "gemini"
        except Exception:
            key = model_override or "gemini"
        left, why = _cooldown_state(key)
        if left > 0:
            raise RuntimeError(
                f"Gemini on cooldown for {left:.0f}s after a recent failure ({why})")
    retried = False
    while True:
        produced = False
        try:
            for event in _stream_openai_once(
                    messages, tools, timeout, provider_override=provider_override,
                    url_override=url_override, model_override=model_override,
                    cancel_event=cancel_event):
                produced = True
                yield event
                if event.get("type") == "cancelled":
                    return
            if escalation:
                with _COOL_LOCK:
                    _GEMINI_COOLDOWNS.pop(key, None)    # healthy again
            return
        except RuntimeError as e:
            if cancel_event is not None and cancel_event.is_set():
                yield {"type": "cancelled", "content": ""}
                return
            if (produced or retried or delay <= 0
                    or not _TRANSIENT_HTTP_RE.search(str(e))):
                if escalation and not produced:
                    _note_gemini_failure(key, str(e))
                raise
            retried = True
            logger.warning("Gemini transient error (%s) - retrying once in %.1fs",
                           str(e)[:120], delay)
            time.sleep(delay)


def gemini_timeout() -> int:
    """Seconds to wait on a Gemini escalation call before falling back to local."""
    try:
        v = int(_load_config().get("gemini_timeout", 15))
    except Exception:
        v = 15
    return max(5, min(v, 120))


def call_llm_stream(
    messages: list,
    tools:    list | None = None,
    timeout:  int = 120,
    *,
    provider_override: str | None = None,
    url_override:      str | None = None,
    model_override:    str | None = None,
    cancel_event=None,
) -> Generator[dict, None, None]:
    """
    Streaming chat request.  Routes to Ollama or OpenAI-compatible backend.

    Normally uses whatever provider/url/model is saved in config. Pass
    provider_override (+ url_override + model_override for non-Ollama
    providers) to route just this one call somewhere else — this is how
    tier_router's HARD-tier escalation sends a single turn to Gemini
    without changing the user's configured default provider.

    Yields:
        {"type": "sentence", "text": str}   — each complete sentence as it arrives
        {"type": "done", "content": str, "tool_calls": list, "stats": dict}
            — when stream ends. stats contains model, prompt_tokens,
              output_tokens, eval_duration (ns), total_duration (ns) —
              any of which may be None if the backend doesn't report it.

    Sentences are split on [.!?] + whitespace so TTS can start immediately.
    Tool calls always appear in the final "done" event.
    """
    provider = provider_override or get_llm_provider()
    if cancel_event is not None and cancel_event.is_set():
        yield {"type": "cancelled", "content": ""}
        return
    if provider_override == "gemini":
        # Cloud escalation must fail fast: a slow 503 used to hold the turn ~30s
        # before the local fallback even started. Config: "gemini_timeout" (s).
        timeout = min(timeout, gemini_timeout())
    if provider != "ollama":
        yield from _stream_openai(
            messages, tools, timeout,
            provider_override=provider, url_override=url_override, model_override=model_override,
            cancel_event=cancel_event,
        )
        return

    url, model = (url_override, model_override) if (url_override and model_override) else get_llm_settings()
    endpoint   = f"{url}/api/chat"

    payload: dict = {
        "model":      model,
        "messages":   messages,
        "stream":     True,
        "keep_alive": -1,
        "think":      False,  # reasoning models (qwen3, deepseek-r1, …) otherwise burn
                               # the whole num_predict budget on a hidden <think> block,
                               # so the generator finishes with no content/tool_calls
                               # and the UI is left stuck on "Thinking".
        # 500 tokens ≈ 350 words — was 150 (≈100 words), which routinely cut
        # ordinary answers off mid-sentence with no indication to the user
        # (see done_reason=="length" handling below). Still bounded so a
        # runaway generation can't hang the turn indefinitely.
        # num_gpu:99 pushes all layers to GPU; num_thread removed (Ollama auto-tunes).
        "options":    {"num_predict": 500, "num_gpu": 99, "num_ctx": _num_ctx()},
    }
    if tools:
        payload["tools"] = tools

    def _do_stream() -> Generator[dict, None, None]:
        with requests.post(endpoint, json=payload, timeout=timeout, stream=True) as resp:
            resp.raise_for_status()
            full_content = ""
            tool_calls:  list = []
            buf          = ""

            for raw in resp.iter_lines():
                if cancel_event is not None and cancel_event.is_set():
                    yield {"type": "cancelled", "content": full_content}
                    return
                if not raw:
                    continue
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg   = chunk.get("message", {})
                delta = msg.get("content") or ""

                full_content += delta
                buf          += delta

                # Yield complete sentences as they accumulate -- but never speak a
                # reply that is (so far) a raw <tool_call> tag; the caller recovers
                # those from the final "content" instead.
                while not _is_tool_tag_prefix(full_content):
                    m = _SENT_END.search(buf)
                    if not m:
                        break
                    sentence = buf[: m.start() + 1].strip()
                    buf      = buf[m.end() :]
                    if sentence:
                        yield {"type": "sentence", "text": sentence}

                tc = msg.get("tool_calls")
                if tc:
                    tool_calls.extend(tc)

                if chunk.get("done"):
                    if buf.strip() and not _is_tool_tag_prefix(full_content):
                        yield {"type": "sentence", "text": buf.strip()}

                    # Ollama reports these on the final chunk (durations in ns).
                    # done_reason == "length" means generation was cut off by
                    # num_predict, not because the model was actually done —
                    # callers should treat that differently from a natural stop.
                    stats = {
                        "model":          model,
                        "prompt_tokens":  chunk.get("prompt_eval_count"),
                        "output_tokens":  chunk.get("eval_count"),
                        "eval_duration":  chunk.get("eval_duration"),       # ns
                        "total_duration": chunk.get("total_duration"),      # ns
                        "truncated":      chunk.get("done_reason") == "length",
                    }

                    yield {
                        "type":       "done",
                        "content":    full_content.strip(),
                        "tool_calls": tool_calls,
                        "stats":      stats,
                    }
                    return

            # Fallback: connection closed without Ollama ever sending a
            # final "done": true chunk. Without this, the caller's loop
            # ends silently with no "done" event, and the UI is left
            # stuck showing "Thinking" forever.
            if buf.strip() and not _is_tool_tag_prefix(full_content):
                yield {"type": "sentence", "text": buf.strip()}
            yield {
                "type":       "done",
                "content":    full_content.strip(),
                "tool_calls": tool_calls,
                "stats":      {"model": model, "prompt_tokens": None, "output_tokens": None,
                                "eval_duration": None, "total_duration": None,
                                # Connection closed before Ollama ever sent a final
                                # "done" chunk — content is very likely incomplete.
                                "truncated": True},
            }

    try:
        yield from _do_stream()
    except requests.exceptions.ConnectionError as e:
        print(f"[LLM] Stream ConnectionError — trying to restart Ollama… ({e})")
        if ensure_ollama_running():
            yield from _do_stream()
            return
        raise RuntimeError(
            f"Cannot connect to Ollama at {url}. "
            "Make sure Ollama is installed and run: ollama serve"
        )
    except requests.exceptions.Timeout:
        raise RuntimeError("Ollama stream timed out.")
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            raise RuntimeError(_ollama_missing_model_message(model, url))
        raise RuntimeError(f"Ollama HTTP error: {e.response.status_code}")
    except Exception as e:
        print(f"[LLM] Stream error: {type(e).__name__}: {e}")
        raise RuntimeError(f"LLM stream failed: {e}")
