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
