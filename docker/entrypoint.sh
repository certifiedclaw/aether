#!/bin/sh
# First-boot setup for the container, then exec the real command.
set -e
umask 077
mkdir -p config/certs data logs memory plugins

# Self-signed TLS cert (the dashboard refuses remote binding without one).
# Replace with your own, or let Traefik terminate TLS in front of it.
if [ ! -f config/certs/aether.key ] || [ ! -f config/certs/aether.crt ]; then
  echo "[entrypoint] generating self-signed dashboard certificate"
  openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj "/CN=aether" \
    -keyout config/certs/aether.key -out config/certs/aether.crt >/dev/null 2>&1
fi

# Seed config from the example, pointing at the Ollama named in the environment.
if [ ! -f config/api_keys.json ]; then
  echo "[entrypoint] seeding config/api_keys.json"
  python - <<'PY'
import json, os
cfg = json.load(open("config/api_keys.example.json"))
url = os.environ.get("OLLAMA_URL", "http://ollama:11434")
for k in ("ollama_url", "llm_url", "llm_local_url"):
    cfg[k] = url
if os.environ.get("AETHER_MODEL"):
    cfg["llm_model"] = os.environ["AETHER_MODEL"]
if os.environ.get("AETHER_REMOTE_OLLAMA"):   # e.g. gaming PC's GPU over Tailscale
    cfg["llm_remote_url"] = os.environ["AETHER_REMOTE_OLLAMA"]
    cfg["llm_remote_model"] = os.environ.get("AETHER_REMOTE_MODEL", cfg.get("llm_model", ""))
    cfg["llm_local_model"] = cfg.get("llm_model", "")
json.dump(cfg, open("config/api_keys.json", "w"), indent=2)
PY
  chmod 600 config/api_keys.json
fi
exec "$@"
