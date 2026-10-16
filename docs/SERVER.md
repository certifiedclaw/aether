# Running Aether on a server (headless)

Headless = no Qt, no mic, no speakers. Voice (STT/TTS) is skipped unless
`AETHER_HEADLESS_VOICE=1`. You talk to it through the web dashboard.

Env switches: `AETHER_HEADLESS=1`, `AETHER_SKIP_INSTALL` (default on when headless:
no pip/browser downloads at boot), `AETHER_DASHBOARD_HOST`, `AETHER_HEADLESS_VOICE`.

## Docker (iansserver)
    cp .env.example .env     # optional: AETHER_BIND=100.102.132.88, AETHER_MODEL=...
    docker compose up -d --build
    docker compose exec ollama ollama pull qwen3:4b
    docker compose logs aether | grep -i dashboard

- Dashboard is HTTPS on :8000 with a self-signed cert (put Traefik/Authelia in front
  and set the backend to skip TLS verify, or mount your own cert in the config volume).
- Bind with `AETHER_BIND=<tailscale ip>`; never expose to the LAN/internet directly.
- Fast GPU on the main PC: set `AETHER_REMOTE_OLLAMA=http://<pc-tailscale-ip>:11434`;
  Aether falls back to the server's CPU model when the PC is off.
- Dependency set: `requirements-server.txt`.
- Not tested in CI: the Docker build itself (no daemon in the sandbox). Report any build error.

## OpenAI-compatible API
Lets editors/scripts use Aether's configured model (remote-GPU with local fallback, leak guard included).

    # config/api_keys.json
    "api_server_enabled": true,
    "api_server_token": "<python -c 'import secrets;print(secrets.token_urlsafe(32))'>"

    curl -k https://<host>:8000/v1/chat/completions -H "Authorization: Bearer <token>" \
      -H "Content-Type: application/json" -d '{"messages":[{"role":"user","content":"hi"}]}'

Text completions only (roles system/user/assistant). Tools, memory, files and approvals are
not reachable. Off until enabled; 10 bad tokens from an IP locks it out for 10 min.
Known limits: `temperature`/`max_tokens` are ignored (Aether's own settings apply); token usage reports 0.

## When the model is down (graceful degradation)
Aether classifies backend failures (Ollama down, model missing, timeout, out of memory, auth, rate limit)
and recovers: retry once after `retry_wait_s` (default 4s), then `fallback_model` if set, then Gemini only if
`cloud_fallback` is true. Otherwise you get a plain-language message in chat, never a silent hang.

## Sharing the box (resource governor)
Scheduled tasks and proactive check-ins pause while the host is busy (Palworld, a game, other containers):
CPU average >= `governor_cpu_busy` (85%) or free RAM < `governor_ram_min_free_mb` (1500). They resume when load
drops, or after `governor_max_defer_s` (1h) so nothing starves. Your own messages are never held back.
`/resources` shows the current state. Inside Docker the host's CPU/RAM are what is measured.

## Audit log
Every gated tool call and approval (allowed / approved / denied / blocked) is appended to `logs/audit.jsonl`
(0600, rotated at 5 MB, 3 files kept). Secrets in parameters are redacted. Each line includes the hash of the
previous one, so edits or deletions are detected (tamper-evident, not tamper-proof).
- Chat: `/audit`, `/audit 50 tool=send_message decision=denied hours=24 words`, `/audit verify`
- Web: `https://<host>:8000/audit` (needs your dashboard login) or `GET /api/audit`
- Config: `audit_log` (default true), `audit_log_safe` (also log safe-tier calls, default false)

## Speed, memory and cloud usage
`/perf [hours]` (chat) or `https://<host>:8000/perf` (dashboard login) shows per model and route (local / remote GPU / cloud):
rounds, time to first token, median and p95 round time, tokens/s, tokens in/out; Aether's memory, host free RAM, and what Ollama has loaded
(RAM vs GPU). Cloud tokens per day are listed; set `cloud_daily_token_budget` for a heads-up before a free tier
runs out (warning only, nothing is blocked). Data: `logs/perf.jsonl`, numbers only - no message text.
