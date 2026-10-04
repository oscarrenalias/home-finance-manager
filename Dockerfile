FROM python:3.11-slim

# curl: container health checks; Reflex installs its own Bun/Node at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependency layer — rebuilt only when pyproject.toml or uv.lock changes
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

# 3000: Reflex-generated Next.js frontend  8000: Reflex backend API + WebSocket
EXPOSE 3000 8000
