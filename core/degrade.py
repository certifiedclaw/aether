"""core/degrade.py - graceful degradation when the model backend fails (roadmap #42).

Pure functions (no I/O) so the policy is easy to test:
  classify(error)         -> Failure(kind, message)  plain-language, actionable
  plan_recovery(...)      -> Action                  what to try next

Policy (per failed model round, attempts 0..2):
  * ollama_down / timeout : wait a few seconds and retry once (it may be restarting
    or loading the model from a slow disk), then fall back if configured
  * model_missing / oom   : switch to config "fallback_model" if set
  * after local options are spent: Gemini only if "cloud_fallback" is true, a
    gemini key exists and cloud_allow is not false (the leak guard still applies)
  * otherwise give up with a clear message - never a silent hang
Config (config/api_keys.json): fallback_model (str), cloud_fallback (bool, default false),
retry_wait_s (default 4).
"""
from __future__ import annotations

from dataclasses import dataclass

OLLAMA_DOWN = "ollama_down"
MODEL_MISSING = "model_missing"
TIMEOUT = "timeout"
OOM = "oom"
AUTH = "auth"
RATE_LIMIT = "rate_limit"
BAD_REQUEST = "bad_request"
CLOUD_BLOCKED = "cloud_blocked"
UNKNOWN = "unknown"

MAX_ATTEMPTS = 4


@dataclass(frozen=True)
class Failure:
    kind: str
    message: str          # what the user hears/sees


@dataclass(frozen=True)
class Action:
    kind: str             # "wait_retry" | "switch_model" | "cloud" | "give_up"
    wait_s: float = 0.0
    model: str = ""
    note: str = ""        # short log line explaining the step


def classify(error) -> Failure:
    raw = str(error or "")
    low = raw.lower()
    if "cloud_allow" in low or ("blocked by config" in low and "cloud" in low):
        return Failure(CLOUD_BLOCKED, "Cloud models are turned off in your settings, so I can only use the local model.")
    if "out of memory" in low or "oom" in low.split() or "cuda error" in low or "insufficient memory" in low:
        return Failure(OOM, "The model ran out of memory. Try a smaller model, or close other heavy programs.")
    if "not found in ollama" in low or ("404" in low and "ollama" in low) or "not installed" in low:
        return Failure(MODEL_MISSING, "The language model isn't installed. Pull it with ollama, or pick another model in settings.")
    if "cannot connect" in low or "connection refused" in low or "failed to establish" in low or "max retries" in low \
            or "connection aborted" in low or "reset by peer" in low or "connection dropped" in low:
        return Failure(OLLAMA_DOWN, "I can't reach the language model. Check that Ollama is running (on the server: docker compose ps).")
    if "timed out" in low or "timeout" in low:
        return Failure(TIMEOUT, "The model took too long to answer. It may still be loading, or the machine is busy - try again in a moment.")
    if "429" in low or "quota" in low or "rate limit" in low:
        return Failure(RATE_LIMIT, "The cloud model is rate-limited right now. I'll use the local model.")
    if "401" in low or "403" in low or "api key" in low or "unauthorized" in low:
        return Failure(AUTH, "The language model rejected the request. Check the API key in settings.")
    if "400" in low:
        return Failure(BAD_REQUEST, "The language model rejected the request. Check the model name in settings.")
    return Failure(UNKNOWN, "Something went wrong talking to the language model. Check the log for details.")


def plan_recovery(failure: Failure, attempt: int, cfg: dict, *, current_model: str = "",
                  on_cloud: bool = False, has_gemini_key: bool = False) -> Action:
    """Decide the next step after a failed round. attempt is 0-based."""
    cfg = cfg or {}
    if failure.kind in (CLOUD_BLOCKED, AUTH, BAD_REQUEST) and not on_cloud:
        return Action("give_up")
    if attempt >= MAX_ATTEMPTS - 1:
        return Action("give_up")
    fb = str(cfg.get("fallback_model") or "").strip()
    if on_cloud:
        return Action("give_up")        # caller already retries cloud failures on the local model

    if failure.kind in (OLLAMA_DOWN, TIMEOUT) and attempt == 0:
        try:
            wait = max(0.0, min(30.0, float(cfg.get("retry_wait_s", 4))))
        except (TypeError, ValueError):
            wait = 4.0
        return Action("wait_retry", wait_s=wait, note=f"{failure.kind}: waiting {wait:g}s then retrying")
    if failure.kind in (MODEL_MISSING, OOM, TIMEOUT) and fb and fb != current_model:
        return Action("switch_model", model=fb, note=f"{failure.kind}: switching to fallback model {fb}")
    cloud_on = str(cfg.get("cloud_fallback", False)).strip().lower() in ("1", "true", "yes", "on")
    if (cloud_on and has_gemini_key and cfg.get("cloud_allow", True) is not False
            and failure.kind in (OLLAMA_DOWN, TIMEOUT, MODEL_MISSING, OOM)):
        return Action("cloud", note=f"{failure.kind}: falling back to the cloud model (cloud_fallback=true)")
    return Action("give_up")
