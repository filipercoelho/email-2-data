# email-2-data — local workspace, containerized.
# Read-only IMAP + LLM triage; serves the FastAPI workspace on 8042 (NEVER 8000).
# Secrets come from a mounted .env (docker-compose env_file) — never baked into the image.
FROM python:3.11-slim

# No .pyc, unbuffered logs (so `docker logs` is live).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install deps first (better layer caching): copy only what pip needs to resolve the package.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[web,vertex]"

# config/, corpus/ and out/ are provided at runtime as volumes (see docker-compose.yml) so the image
# carries no inbox data or secrets. gazetteer.csv etc. live under config/ and are mounted read-only.

# Runs as root by design. This is a single-user 127.0.0.1-loopback workspace, and out/ + corpus/ are
# host BIND MOUNTS — a non-root container UID typically can't write a host-owned bind mount (the app
# would fail to persist the precious workspace.db), which is a worse failure than root here. To switch
# to non-root for a server deploy, add a USER with a UID matching the host volume owner AND point the
# Vertex ADC mount at that user's HOME (compose maps to /root/.config/gcloud, which only fits root).

EXPOSE 8042
# Liveness: a crash-looping boot (e.g. a missing/empty volume) never answers /healthz, so the
# container is reported unhealthy instead of silently restart-looping. Uses python (no curl in slim).
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8042/healthz', timeout=2).status==200 else 1)"

# Bind 0.0.0.0 so the host can reach it through the published port; compose maps it to 127.0.0.1 only.
CMD ["email2data", "serve", "--port", "8042", "--host", "0.0.0.0"]
