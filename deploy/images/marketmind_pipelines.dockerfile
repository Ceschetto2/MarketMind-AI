# Immagine delle pipeline di ingestion: solo `marketmind-pipelines` e le sue
# dipendenze di workspace (`marketmind-common`, `marketmind-db`), niente
# vectorbt/streamlit/google-genai del Decision Engine e della dashboard.
# Un container Quadlet per pipeline, tutti su questa immagine, ciascuno con
# il proprio `Exec=python -m marketmind_pipelines run <nome>`.
#
# Build (dalla root del repo):
#   podman build -t marketmind-pipelines:latest -f deploy/images/marketmind_pipelines.dockerfile .

# --- build: risolve e installa nel venv, poi viene scartato ---
FROM python:3.14-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Prima solo i manifest del workspace (root + tutti i membri: il lock li
# referenzia tutti) e le sole dipendenze di terze parti: questo layer si
# invalida solo quando cambiano pyproject.toml/uv.lock, non a ogni modifica
# del codice.
COPY pyproject.toml uv.lock README.md ./
COPY packages/marketmind_common/pyproject.toml packages/marketmind_common/
COPY packages/marketmind_db/pyproject.toml packages/marketmind_db/
COPY packages/marketmind_pipelines/pyproject.toml packages/marketmind_pipelines/
COPY packages/marketmind_llm_decision_engine/pyproject.toml packages/marketmind_llm_decision_engine/
COPY packages/marketmind_frontend/pyproject.toml packages/marketmind_frontend/
RUN uv sync --frozen --no-dev --package marketmind-pipelines --no-install-workspace

# Poi il codice dei soli pacchetti che servono, installati come wheel (non
# editable): il runtime non ha bisogno dei sorgenti.
COPY packages/marketmind_common packages/marketmind_common
COPY packages/marketmind_db packages/marketmind_db
COPY packages/marketmind_pipelines packages/marketmind_pipelines
RUN uv sync --frozen --no-dev --package marketmind-pipelines --no-editable

# --- runtime: solo Python e il venv ---
FROM python:3.14-slim

COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

# Nessun utente root a runtime.
RUN useradd --system --no-create-home marketmind
USER marketmind
WORKDIR /app

# Il comando arriva da `Exec=` di ogni unit Quadlet; senza argomenti elenca
# le pipeline disponibili.
ENTRYPOINT ["python", "-m", "marketmind_pipelines"]
CMD ["list"]
