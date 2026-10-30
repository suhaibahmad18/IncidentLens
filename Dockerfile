# IncidentLens production image: FastAPI API + built React frontend in one container.

# --- 1. Build the frontend -------------------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- 2. Python runtime -----------------------------------------------------------------
FROM python:3.13-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed \
    FRONTEND_DIST=/app/frontend/dist \
    PATH="/app/backend/.venv/bin:$PATH"

# Built for HTTPS deployments: Secure, __Host- session cookies and the demo-data banner. For a
# plain-HTTP local container set PUBLIC_DEMO_MODE=false (or SESSION_COOKIE_SECURE=false), otherwise
# the browser never sends the session cookie back and sign-in appears to do nothing.
ENV PUBLIC_DEMO_MODE=true

# Unprivileged user owns /app from the start (no chown -R layer that duplicates files).
RUN useradd --create-home --uid 10001 incidentlens && mkdir /app && chown incidentlens /app
USER incidentlens

WORKDIR /app/backend
COPY --chown=incidentlens backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=incidentlens backend/ ./
# Bake the embedding model into the image: no download at runtime, faster cold starts.
RUN python -c "from app.rag.embeddings import FastEmbedEmbedder; FastEmbedEmbedder().embed(['warm up'])"

COPY --chown=incidentlens --from=frontend /build/frontend/dist /app/frontend/dist

EXPOSE 8000
# Liveness only (no database call); platforms should use /api/health/ready for readiness.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/api/health', timeout=4)"

# Web service (default). PORT is provided by most platforms; 8000 locally.
# The SAME image runs the one-off release job with a different command and environment:
#     python -m app.db.release      (needs DATABASE_ADMIN_URL; the web service must not have it)
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
