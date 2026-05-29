# Open Persona — `persona-api` production image (Spec 11, T10)
#
# Single uvicorn worker (S08-4 / D-08-5): the in-process agentic-run event bus
# and the in-memory rate limiter require it. Migrations are run manually
# (spec 07, §7) — NOT on container start — to keep deploys explicit.

# ---------- builder ----------
FROM python:3.12-slim AS builder
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /usr/local/bin/uv

WORKDIR /app
# Copy workspace pyproject + lock first to leverage the layer cache.
COPY pyproject.toml uv.lock ./
COPY packages/core/pyproject.toml packages/core/pyproject.toml
COPY packages/runtime/pyproject.toml packages/runtime/pyproject.toml
COPY packages/api/pyproject.toml packages/api/pyproject.toml
# Provision the venv with all workspace members (frozen install).
COPY packages/ packages/
RUN uv sync --frozen --all-packages --no-dev

# ---------- runtime ----------
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home --shell /bin/bash persona

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app

WORKDIR /app
USER persona

# Audit / turnlog JSONL roots — bind-mount a volume in production.
ENV PERSONA_AUDIT_ROOT=/var/lib/persona/audit \
    PERSONA_TURNLOG_PATH=/var/lib/persona/turnlogs

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

# Single uvicorn worker (S08-4). Set proxy headers because the VPS terminates TLS upstream.
CMD ["uvicorn", "persona_api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--proxy-headers", "--forwarded-allow-ips=*"]
