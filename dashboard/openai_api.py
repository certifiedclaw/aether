"""dashboard/openai_api.py - OpenAI-compatible endpoint for Aether's models (roadmap #41).

Lets other tools (editors, scripts, Open WebUI, etc.) use whatever model Aether
is configured for - including its remote-GPU-with-local-fallback routing and the
leak guard - through POST /v1/chat/completions and GET /v1/models.

Scope: plain model access. It does NOT expose Aether's tools, memory, files or
approvals; a client can only get text completions. Off by default.

Config (config/api_keys.json):
    api_server_enabled : true to mount the routes
    api_server_token   : bearer token, at least 24 chars (clients send
                         "Authorization: Bearer <token>")
"""

import hmac
import json
import time
import uuid
from typing import Any, Callable, Iterator

MIN_TOKEN_LEN = 24
MAX_MESSAGES = 100
MAX_CHARS = 200_000
_MAX_FAILS = 10
_FAIL_WINDOW = 300.0
_LOCKOUT = 600.0


class _FailLimiter:
    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._fails: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}

    def locked(self, ip: str) -> bool:
        until = self._locked.get(ip, 0.0)
        if until > self._clock():
            return True
        self._locked.pop(ip, None)
        return False

    def fail(self, ip: str) -> None:
        now = self._clock()
        recent = [t for t in self._fails.get(ip, []) if now - t < _FAIL_WINDOW] + [now]
        self._fails[ip] = recent
        if len(recent) >= _MAX_FAILS:
            self._locked[ip] = now + _LOCKOUT
            self._fails.pop(ip, None)
        if len(self._fails) > 1000:
            self._fails.clear()


def api_enabled(cfg: dict) -> bool:
    tok = cfg.get("api_server_token")
    on = cfg.get("api_server_enabled")
    if isinstance(on, str):
        on = on.strip().lower() in ("1", "true", "yes", "on")
    return bool(on) and isinstance(tok, str) and len(tok) >= MIN_TOKEN_LEN


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # multimodal parts: keep text only
        return "\n".join(p.get("text", "") for p in content
                         if isinstance(p, dict) and p.get("type") == "text")
    return ""


def clean_messages(raw: Any) -> list[dict]:
    """Validate and normalise client messages. Raises ValueError on bad input."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("messages must be a non-empty list")
    if len(raw) > MAX_MESSAGES:
        raise ValueError(f"too many messages (max {MAX_MESSAGES})")
    out, total = [], 0
    for m in raw:
        if not isinstance(m, dict):
            raise ValueError("each message must be an object")
        role = m.get("role")
        if role not in ("system", "user", "assistant"):
            raise ValueError(f"unsupported role {role!r}")
        text = _text_of(m.get("content"))
        total += len(text)
        if total > MAX_CHARS:
            raise ValueError(f"messages too long (max {MAX_CHARS} characters)")
        out.append({"role": role, "content": text})
    if out[-1]["role"] != "user":
        raise ValueError("last message must have role 'user'")
    return out


def build_router(get_cfg: Callable[[], dict], stream_fn: Callable[..., Iterator[dict]],
                 model_name_fn: Callable[[], str], limiter: _FailLimiter | None = None):
    """Create the APIRouter. stream_fn is core.llm_client.call_llm_stream-shaped."""
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse, StreamingResponse

    router = APIRouter()
    lim = limiter or _FailLimiter()

    def _err(status: int, msg: str, kind: str = "invalid_request_error") -> JSONResponse:
        return JSONResponse({"error": {"message": msg, "type": kind}}, status_code=status)

    def _auth(request: Request) -> JSONResponse | None:
        ip = request.client.host if request.client else "?"
        if lim.locked(ip):
            return _err(429, "too many failed attempts", "rate_limit_error")
        cfg = get_cfg()
        if not api_enabled(cfg):
            return _err(404, "API server is disabled")
        header = request.headers.get("authorization", "")
        presented = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not hmac.compare_digest(presented.encode(), str(cfg["api_server_token"]).encode()):
            lim.fail(ip)
            return _err(401, "invalid API key", "authentication_error")
        return None

    @router.get("/v1/models")
    async def models(request: Request):
        bad = _auth(request)
        if bad:
            return bad
        return {"object": "list", "data": [
            {"id": model_name_fn(), "object": "model", "created": 0, "owned_by": "aether"}]}

    @router.post("/v1/chat/completions")
    async def chat(request: Request):
        bad = _auth(request)
        if bad:
            return bad
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
            messages = clean_messages(body.get("messages"))
        except ValueError as e:
            return _err(400, str(e))
        except Exception:  # noqa: BLE001 - malformed JSON
            return _err(400, "invalid JSON body")

        model = model_name_fn()
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())

        def chunk(delta: dict, finish: str | None = None) -> str:
            return "data: " + json.dumps({
                "id": cid, "object": "chat.completion.chunk", "created": created,
                "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }) + "\n\n"

        if body.get("stream"):
            def gen() -> Iterator[str]:
                yield chunk({"role": "assistant"})
                try:
                    for ev in stream_fn(messages, None):
                        if ev.get("type") == "sentence":
                            yield chunk({"content": ev["text"] + " "})
                        elif ev.get("type") == "done":
                            break
                except Exception as e:  # noqa: BLE001
                    yield chunk({"content": f"[error: {type(e).__name__}]"}, "stop")
                    yield "data: [DONE]\n\n"
                    return
                yield chunk({}, "stop")
                yield "data: [DONE]\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream")

        def run() -> str:
            for ev in stream_fn(messages, None):
                if ev.get("type") == "done":
                    return ev.get("content", "")
            return ""

        import asyncio
        try:
            text = await asyncio.get_running_loop().run_in_executor(None, run)
        except Exception as e:  # noqa: BLE001
            return _err(502, f"model backend failed: {type(e).__name__}", "api_error")
        return {"id": cid, "object": "chat.completion", "created": created, "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}

    return router
