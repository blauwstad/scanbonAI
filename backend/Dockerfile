# =============================================================================
# ScanbonAI – Backend Dockerfile (FastAPI + Celery)
# =============================================================================
# Multi-stage build:
#   builder  – installs Python dependencies into a venv
#   runtime  – lean final image; copies the venv + app code
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: builder – compile Python wheels, build native extensions
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

# Prevent .pyc files and buffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install build-time system dependencies:
#   libpq-dev        – required to compile psycopg / asyncpg C extension
#   gcc / python3-dev – C compiler for native wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev \
        gcc \
        python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Create isolated virtual environment so we can copy it cleanly to runtime
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies first (layer-cached until requirements change)
COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2: runtime – minimal production image
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    # Point the app at the mounted invoice storage (overridable via env)
    STORAGE_PATH=/data/invoices

WORKDIR /app

# Runtime system dependencies:
#   libpq5           – PostgreSQL client library (runtime, not dev headers)
#   libgl1           – OpenGL shared library required by OpenCV at import time
#   libglib2.0-0     – GLib runtime required by OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libgl1 \
        libglib2.0-0 \
        # Minimal curl for Docker healthcheck
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the pre-built virtual environment from the builder stage
COPY --from=builder /opt/venv /opt/venv

# Create a non-root user and group for running the app
RUN groupadd --gid 1000 appgroup && \
    useradd --uid 1000 --gid appgroup --shell /bin/bash --create-home appuser

# Create the invoice storage directory with correct ownership
RUN mkdir -p /data/invoices && chown -R appuser:appgroup /data/invoices

# Copy application source code
COPY --chown=appuser:appgroup ./app ./app

# Switch to non-root user for all subsequent instructions and at runtime
USER appuser

# ---------------------------------------------------------------------------
# Expose the uvicorn port (informational; actual port binding is in compose)
# ---------------------------------------------------------------------------
EXPOSE 8000

# ---------------------------------------------------------------------------
# Default command – Celery worker overrides this in docker-compose.yml
# ---------------------------------------------------------------------------
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--loop", "uvloop", "--no-access-log"]
