"""Test di integrazione della coda dei job (`marketmind_frontend.jobs.JobQueue`)
contro Postgres reale: richieste scritte dalla dashboard (policy `APP`) e
prese in carico dal dispatcher.

Tutto gira sulla transazione annullata di `rollback_connection`; le righe già
presenti in `ops.t_job_requests` (richieste vere) si escludono confrontando
solo gli id creati dal test, e il claim lavora su un istante futuro, così le
richieste vere pendenti risultano scadute invece di essere prese in carico.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from marketmind_db.database import Database
from marketmind_db.exceptions import AccessDeniedError
from marketmind_frontend.jobs import REQUEST_TTL, InvalidJobError, JobQueue

pytestmark = pytest.mark.integration

NOW = datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture
def app(rollback_app_db: Database) -> Database:
    return rollback_app_db


@pytest.fixture
def ro(rollback_readonly_db: Database) -> Database:
    return rollback_readonly_db


class TestRequest:
    def test_pipeline_pending(self, app):
        with app.transaction() as tx:
            request, created = JobQueue(tx).request("pipeline", "fred", now=NOW)

        assert created
        assert (request.kind, request.target, request.status) == ("pipeline", "fred", "pending")
        assert request.label == "pipeline fred"

    def test_richiesta_uguale_pendente_non_si_duplica(self, app):
        with app.transaction() as tx:
            queue = JobQueue(tx)
            first, _ = queue.request("backtest", 4, now=NOW)
            again, created = queue.request("backtest", "4", now=NOW + timedelta(seconds=5))

        assert not created and again.job_request_id == first.job_request_id

    def test_decide_senza_target(self, app):
        with app.transaction() as tx:
            request, _ = JobQueue(tx).request("decide", now=NOW)

        assert request.target is None and request.label == "giro del Decision Engine"

    @pytest.mark.parametrize(
        "kind, target",
        [("pipeline", "inesistente"), ("pipeline", None), ("backtest", 0), ("init-portfolio", "abc"),
         ("decide", "4"), ("rm -rf", None)],
    )
    def test_richieste_non_valide(self, app, kind, target):
        with app.transaction() as tx:
            with pytest.raises(InvalidJobError):
                JobQueue(tx).request(kind, target, now=NOW)

    def test_sola_lettura_non_accoda(self, ro):
        with ro.transaction() as tx:
            with pytest.raises(AccessDeniedError):
                JobQueue(tx).request("decide", now=NOW)


class TestClaim:
    def test_in_ordine_di_richiesta_e_poi_niente(self, app):
        later = NOW + REQUEST_TTL * 1000
        with app.transaction() as tx:
            queue = JobQueue(tx)
            a, _ = queue.request("pipeline", "fred", now=later)
            b, _ = queue.request("init-portfolio", 7, now=later + timedelta(seconds=1))
        with app.transaction() as tx:
            queue = JobQueue(tx)
            first = queue.claim(now=later + timedelta(seconds=10))
            second = queue.claim(now=later + timedelta(seconds=11))
            third = queue.claim(now=later + timedelta(seconds=12))

        assert (first.job_request_id, first.status) == (a.job_request_id, "started")
        assert first.started_at == later + timedelta(seconds=10)
        assert second.job_request_id == b.job_request_id and second.target == "7"
        assert third is None

    def test_richieste_troppo_vecchie_scadono(self, app):
        later = NOW + REQUEST_TTL * 1000
        with app.transaction() as tx:
            stale, _ = JobQueue(tx).request("pipeline", "fred", now=later)
        with app.transaction() as tx:
            queue = JobQueue(tx)
            claimed = queue.claim(now=later + REQUEST_TTL + timedelta(minutes=1))
            [expired] = [r for r in queue.recent() if r.job_request_id == stale.job_request_id]

        assert claimed is None
        assert expired.status == "failed" and "scaduta" in expired.error_message

    def test_fail_registra_lerrore(self, app):
        with app.transaction() as tx:
            queue = JobQueue(tx)
            request, _ = queue.request("decide", now=NOW)
            queue.fail(request.job_request_id, "unit non trovata", now=NOW)
            [failed] = [r for r in queue.recent() if r.job_request_id == request.job_request_id]

        assert (failed.status, failed.error_message) == ("failed", "unit non trovata")

    def test_ultima_richiesta_per_job(self, app, ro):
        with app.transaction() as tx:
            queue = JobQueue(tx)
            queue.request("backtest", 4, now=NOW)
        with ro.transaction() as tx:
            latest = JobQueue(tx).latest_for("backtest", 4)

        assert latest is not None and latest.status == "pending"
