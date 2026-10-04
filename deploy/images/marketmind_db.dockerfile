# Immagine delle migrazioni Alembic (`marketmind-migrate`): il DB stesso gira
# sull'immagine ufficiale TimescaleDB (`marketmind-db.container`), questa
# immagine contiene solo `marketmind-db` (modelli, Alembic) e le migrazioni
# di `deploy/alembic/`. Eseguita come container one-shot da
# `marketmind-migrate.container`, prima che partano pipeline e Decision
# Engine: lo schema non si aggiorna più dall'host.
#
# Build (dalla root del repo):
#   podman build -t marketmind-migrate:latest -f deploy/images/marketmind_db.dockerfile .

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
RUN uv sync --frozen --no-dev --package marketmind-db --no-install-workspace

COPY packages/marketmind_common packages/marketmind_common
COPY packages/marketmind_db packages/marketmind_db
RUN uv sync --frozen --no-dev --package marketmind-db --no-editable

FROM python:3.14-slim

COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

RUN useradd --system --no-create-home marketmind
USER marketmind
WORKDIR /app

# Alembic legge `[tool.alembic]` dal pyproject.toml della directory
# corrente (script_location = "deploy/alembic").
COPY pyproject.toml ./
COPY deploy/alembic deploy/alembic

ENTRYPOINT ["alembic"]
CMD ["upgrade", "head"]
