# ── Stage 1: build frontend ──────────────────────────────────────────────────
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci --ignore-scripts
COPY frontend/ .
RUN npm run build

# ── Stage 2: Python runtime ───────────────────────────────────────────────────
FROM python:3.11-slim AS runtime
WORKDIR /app

# trafilatura needs lxml which needs libxml2/libxslt
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libxml2-dev libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python package with media extras (YouTube transcript, trafilatura)
COPY pyproject.toml ./
COPY mymem/ ./mymem/
RUN pip install --no-cache-dir ".[media]"

# Default config — overridden by volume mount at runtime
COPY config.yaml ./

# Frontend built assets
COPY --from=frontend /build/dist ./frontend/dist

# Persistent directories — mount these as volumes
RUN mkdir -p data wiki raw outputs/charts outputs/slides

EXPOSE 7860

CMD ["mymem", "serve", "--host", "0.0.0.0", "--port", "7860", "--no-open"]
