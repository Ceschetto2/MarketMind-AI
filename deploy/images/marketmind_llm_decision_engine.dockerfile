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
# vectorbt compila con numba e ne salva la cache accanto ai sorgenti, in
# site-packages: non scrivibile da un utente non root. NUMBA_CACHE_DIR la
# sposta in una directory scrivibile.
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    NUMBA_CACHE_DIR=/tmp/numba-cache

RUN useradd --system --no-create-home marketmind
USER marketmind
WORKDIR /app

# Il comando arriva da `Exec=` della unit Quadlet; di default il giro del
# timer condiviso (`run-due`).
ENTRYPOINT ["python", "-m", "marketmind_llm_decision_engine"]
CMD ["run-due"]
