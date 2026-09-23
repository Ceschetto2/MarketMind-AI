"""Test di integrazione per l'`ON DELETE` della FK `t_audit_logs.run_id`.

Copre il fix della migrazione `0015`: prima del fix, cancellare una riga da
`t_ingestion_runs` falliva sempre non appena esisteva almeno una riga in
`t_audit_logs` che la referenziava — bloccando anche la pulizia più
ordinaria di un run di test. `run_id` è nullable per disegno (rappresenta
"nessun run noto"), quindi `ON DELETE SET NULL` è coerente con la semantica
già esistente della colonna: cancellare il run scollega l'audit log invece
di romperne la cancellazione o perdere la riga di storico.

Usa la fixture `db_session` (rollback automatico a fine test).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from marketmind_ai.db.models.audit import AuditLog, IngestionRun

pytestmark = pytest.mark.integration

TEST_RUN_ID = -1


def _make_run(session, run_id: int = TEST_RUN_ID) -> int:
    run = IngestionRun(
        run_id=run_id,
        source="yfinance",
        target_table="market_data.t_market_prices",
        started_at=datetime.now(timezone.utc),
        status="success",
    )
    session.add(run)
    session.flush()
    return run.run_id


def _make_audit_log(session, run_id: int) -> int:
    log = AuditLog(
        run_id=run_id,
        schema_name="market_data",
        table_name="t_assets",
        row_pk="1",
        operation="INSERT",
    )
    session.add(log)
    session.flush()
    return log.audit_id


class TestDeleteIngestionRunWithAuditLogs:
    def test_delete_succeeds_instead_of_failing(self, db_session):
        run_id = _make_run(db_session)
        _make_audit_log(db_session, run_id)

        db_session.execute(delete(IngestionRun).where(IngestionRun.run_id == run_id))
        db_session.flush()

    def test_audit_log_row_survives_with_null_run_id(self, db_session):
        run_id = _make_run(db_session)
        audit_id = _make_audit_log(db_session, run_id)

        db_session.execute(delete(IngestionRun).where(IngestionRun.run_id == run_id))
        db_session.flush()

        row = db_session.execute(
            select(AuditLog).where(AuditLog.audit_id == audit_id)
        ).scalar_one()
        assert row.run_id is None
