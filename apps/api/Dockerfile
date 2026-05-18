# Multi-stage build for the Altera AI FastAPI backend.
#
# Stage 1 — install dependencies via uv into a virtual environment.
# Stage 2 — copy only the venv and source into a lean runtime image.
#
# Build:
#   docker build -t altera-api apps/api/
#
# Run locally (uses in-memory store, dev auth):
#   docker run --rm -p 8000:8000 \
#     -e ALTERA_DEV_AUTH_ENABLED=true \
#     -e CORS_ALLOWED_ORIGINS=http://localhost:3000 \
#     altera-api
#
# Production — pass real secrets via environment variables or secret manager.
# Never bake secrets into the image.

# ── Stage 1: dependency installer ────────────────────────────────────────
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

WORKDIR /app

# Copy only dependency manifests first — maximises layer cache reuse.
COPY pyproject.toml uv.lock ./

# Install production dependencies into .venv, skip dev extras.
RUN uv sync --frozen --no-dev

# ── Stage 2: runtime image ────────────────────────────────────────────────
FROM python:3.11-slim-bookworm

WORKDIR /app

# Non-root user for defence-in-depth.
RUN addgroup --system altera && adduser --system --ingroup altera altera

# Copy the populated virtual environment from the builder stage.
COPY --from=builder /app/.venv /app/.venv

# Copy application source.
COPY altera_api/ ./altera_api/

# Make the venv the active Python interpreter.
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"

# Disable Python bytecode writing (optional, saves a tiny bit of disk).
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Expose the default port. Override with the $PORT env var at runtime
# (Render / Fly.io / Railway all set $PORT automatically).
EXPOSE 8000

USER altera

# Start the ASGI server. $PORT overrides 8000 if set by the hosting platform.
CMD ["sh", "-c", "exec uvicorn altera_api.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
