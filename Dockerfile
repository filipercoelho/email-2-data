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

EXPOSE 8042
# Bind 0.0.0.0 so the host can reach it through the published port; compose maps it to 127.0.0.1 only.
CMD ["email2data", "serve", "--port", "8042", "--host", "0.0.0.0"]
