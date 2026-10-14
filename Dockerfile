# Aether headless server. Build:  docker compose build aether
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    AETHER_HEADLESS=1 AETHER_SKIP_INSTALL=1 AETHER_SKIP_MAINTENANCE=1 \
    AETHER_DASHBOARD_HOST=0.0.0.0

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssl libsndfile1 curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 aether

WORKDIR /app
COPY requirements-server.txt .
RUN pip install -r requirements-server.txt

COPY --chown=aether:aether . .
RUN chmod +x docker/entrypoint.sh \
 && mkdir -p config data logs memory plugins \
 && chown -R aether:aether /app

USER aether
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD curl -fsk https://127.0.0.1:8000/login -o /dev/null || exit 1
ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["python", "main.py"]
