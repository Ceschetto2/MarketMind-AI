# Immagine del Decision Engine: `marketmind-llm-decision-engine` e le sue
# dipendenze (google-genai, vectorbt, ...). Usata da
# `marketmind-decide.container`, lanciato dal timer condiviso.
#
# Build (dalla root del repo):
#   podman build -t marketmind-llm-decision-engine:latest -f deploy/images/marketmind_llm_decision_engine.dockerfile .

FROM python:3.14-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY packages/marketmind_common/pyproject.toml packages/marketmind_common/
COPY packages/marketmind_db/pyproject.toml packages/marketmind_db/
COPY packages/marketmind_pipelines/pyproject.toml packages/marketmind_pipelines/
COPY packages/marketmind_llm_decision_engine/pyproject.toml packages/marketmind_llm_decision_engine/
COPY packages/marketmind_frontend/pyproject.toml packages/marketmind_frontend/
RUN uv sync --frozen --no-dev --package marketmind-llm-decision-engine --no-install-workspace

COPY packages/marketmind_common packages/marketmind_common
COPY packages/marketmind_db packages/marketmind_db
COPY packages/marketmind_llm_decision_engine packages/marketmind_llm_decision_engine
RUN uv sync --frozen --no-dev --package marketmind-llm-decision-engine --no-editable

FROM python:3.14-slim

COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

RUN useradd --system --no-create-home marketmind
USER marketmind
WORKDIR /app

ENTRYPOINT ["python", "-m"]
CMD ["marketmind_llm_decision_engine.decision_engine.pipeline"]
