# Build context: agents/ (shared dependencies + the aigateway package)
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PYTHONPATH=/app/common
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY common ./common
COPY orchestrator ./orchestrator
WORKDIR /app/orchestrator
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
