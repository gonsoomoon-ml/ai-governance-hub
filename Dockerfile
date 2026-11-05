# AI Governance Hub — FastAPI + Jinja2 on uvicorn. Same image for EKS (Fargate) or any container host.
# Build:  docker build -t governance-hub:dev .     Run: docker run --rm -p 8080:8080 --env-file .env governance-hub:dev
FROM python:3.13-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /app

# dependencies first (cached unless pyproject/uv.lock change); --extra hub = web server deps, no dev tools
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra hub

COPY hub ./hub
# the venv stays root-owned (world-readable); the service user only needs read + execute
RUN useradd --create-home --uid 10001 hub
USER hub
ENV PATH="/app/.venv/bin:$PATH" HUB_PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"
CMD ["python", "-m", "hub"]
