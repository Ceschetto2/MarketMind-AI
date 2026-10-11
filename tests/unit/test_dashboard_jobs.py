"""Test unitari della parte pura della coda dei job (`marketmind_frontend.jobs`):
validazione delle richieste ed elenco delle pipeline. La coda vera e propria
è testata contro Postgres in `tests/integration/test_job_queue_db.py`."""

from __future__ import annotations

from pathlib import Path

import pytest

from marketmind_db.access import APP, FULL_ACCESS, INGESTION, READ_ONLY
from marketmind_frontend.jobs import PIPELINES, InvalidJobError, JobRequest, normalize_job

REPO = Path(__file__).resolve().parents[2]


def test_pipelines_coincidono_con_le_unit_installate_dal_deploy():
    """Il container della dashboard non vede systemd: l'elenco è una costante,
    che deve restare allineata alle unit Quadlet delle pipeline."""
    units = sorted(
        p.name.removeprefix("marketmind-ingest-").removesuffix(".container")
        for p in (REPO / "deploy/quadlet/marketmind_pipelines").glob("marketmind-ingest-*.container")
    )
    assert sorted(PIPELINES) == units


@pytest.mark.parametrize(
    "kind, target, expected",
    [("pipeline", "fred", "fred"), ("decide", None, None), ("backtest", 4, "4"), ("init-portfolio", "12", "12")],
)
def test_normalize_job(kind, target, expected):
    assert normalize_job(kind, target) == (kind, expected)


@pytest.mark.parametrize(
    "kind, target",
    [("pipeline", "fred; rm -rf ~"), ("pipeline", None), ("decide", "x"), ("backtest", -1),
     ("backtest", "4 5"), ("init-portfolio", None), ("sconosciuto", None)],
)
def test_normalize_job_rifiuta(kind, target):
    with pytest.raises(InvalidJobError):
        normalize_job(kind, target)


def test_etichette():
    def req(kind, target):
        return JobRequest(1, kind, target, None, "pending", None, None)

    assert req("pipeline", "fred").label == "pipeline fred"
    assert req("decide", None).label == "giro del Decision Engine"
    assert req("init-portfolio", "5").label == "bootstrap del portfolio 5"
    assert req("backtest", "5").label == "backtest del portfolio 5"


def test_solo_app_scrive_le_richieste():
    assert "ops" in APP.writable_schemas and "ops" in FULL_ACCESS.writable_schemas
    assert "ops" not in INGESTION.writable_schemas and not READ_ONLY.writable_schemas
