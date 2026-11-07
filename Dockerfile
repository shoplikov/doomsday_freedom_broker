# CQA: one image serves the REST API and the static UI (web/).
# Stages: base (runtime deps) -> app (default) ; test (adds pytest, runs the suite)

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
# Dependencies first: this layer is rebuilt only when pyproject.toml / uv.lock change.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY cqa ./cqa
COPY web ./web
COPY data/checklist_v1.json ./data/checklist_v1.json
COPY data/fixtures/SAMPLE.json ./data/fixtures/SAMPLE.json

FROM base AS test
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project
COPY tests ./tests
ENV LLM_PROVIDER=mock
CMD ["pytest", "-q", "-p", "no:cacheprovider"]

FROM base AS app
# Mutable state lives outside the code: SQLite, logs, cached API responses -> one volume.
ENV CQA_DB_PATH=/var/cqa/cqa.sqlite3 CQA_LOG_DIR=/var/cqa/logs CQA_RAW_DIR=/var/cqa/raw
RUN useradd --create-home --uid 10001 cqa && mkdir -p /var/cqa && chown -R cqa /var/cqa
USER cqa
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
CMD ["uvicorn", "cqa.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
