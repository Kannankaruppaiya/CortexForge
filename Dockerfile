# CortexForge: Production Dockerfile
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Install build dependencies, git, and Node.js for dashboard build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --upgrade pip \
    && pip install -e ".[all]"

# Build Frontend Dashboard
COPY apps/web/package.json apps/web/package-lock.json ./apps/web/
WORKDIR /app/apps/web
RUN npm ci
COPY apps/web/ ./
RUN npm run build

# Final Runtime Image
FROM python:3.12-slim-bookworm AS runner

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CORTEX_HOST=0.0.0.0 \
    CORTEX_PORT=8000 \
    CORTEX_ENV=production \
    CORTEX_WORKSPACE_ROOT=/workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create non-root user and persistent workspace directory (§17)
RUN groupadd -g 1001 cortexforge \
    && useradd -u 1001 -g cortexforge -m -s /bin/bash cortexforge \
    && mkdir -p /app /workspace \
    && chown -R cortexforge:cortexforge /app /workspace

# Copy python virtual environment/installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy source and built frontend assets
COPY pyproject.toml README.md alembic.ini ./
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY --from=builder /app/apps/web/dist ./apps/web/dist

# Install project package and ensure ownership
RUN pip install --no-deps -e . \
    && chown -R cortexforge:cortexforge /app

USER cortexforge

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Enforce Alembic as runtime schema authority before serving traffic (§16)
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn cortexforge.apps.api.main:app --host 0.0.0.0 --port 8000"]
