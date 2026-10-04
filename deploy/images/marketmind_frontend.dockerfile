# Immagine della dashboard Streamlit (`marketmind-frontend`). Non ha ancora
# una unit Quadlet: la containerizzazione della dashboard è rimandata, oggi
# gira in locale (`uv run streamlit run packages/marketmind_frontend/src/app.py`).
#
# Build (dalla root del repo):
#   podman build -t marketmind-frontend:latest -f deploy/images/marketmind_frontend.dockerfile .

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
RUN uv sync --frozen --no-dev --package marketmind-frontend --no-install-workspace

COPY packages/marketmind_common packages/marketmind_common
COPY packages/marketmind_db packages/marketmind_db
COPY packages/marketmind_frontend packages/marketmind_frontend
RUN uv sync --frozen --no-dev --package marketmind-frontend --no-editable

FROM python:3.14-slim

COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

RUN useradd --system --no-create-home marketmind
USER marketmind
WORKDIR /app

EXPOSE 8501
# `streamlit run` vuole il percorso di uno script, non un modulo: quello
# installato nel venv.
CMD ["sh", "-c", "exec streamlit run \"$(python -c 'import marketmind_frontend.app as m; print(m.__file__)')\" --server.address=0.0.0.0 --server.port=8501 --server.headless=true"]
