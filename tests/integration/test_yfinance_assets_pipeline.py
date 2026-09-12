"""Test di integrazione per `run()` di `yfinance_assets_pipeline.py`.

Vera chiamata a `yfinance` (nessuna chiave, nessun costo) per l'universo
reale (26 ticker già in `t_universe_members` da `universe-csv`) — coerente
con l'approccio già usato per `yfinance-prices`. `run()` apre le proprie
sessioni via `get_session()`: qui non serve nemmeno pulizia esplicita per i
dati stessi (l'anagrafica aggiornata è un miglioramento legittimo dei dati
reali, non uno scarto di test), solo per la riga di audit.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from marketmind_ai.db.models.audit import IngestionRun
from marketmind_ai.db.models.market_data import Asset
from marketmind_ai.db.session import get_session
from marketmind_ai.ingestion.yfinance_assets_pipeline import run

pytestmark = pytest.mark.integration


class TestRunEndToEnd:
    def test_run_aggiorna_anagrafica_reale(self):
        with get_session() as session:
            run_ids_before = set(
                session.execute(
                    select(IngestionRun.run_id).where(
                        IngestionRun.target_table == "market_data.t_assets"
                    )
                ).scalars()
            )

        run()

        with get_session() as session:
            aapl = session.execute(
                select(Asset).where(Asset.symbol == "AAPL")
            ).scalar_one()
            assert aapl.name  # popolato da longName, non vuoto
            assert aapl.asset_type == "equity"

            audit_row = session.execute(
                select(IngestionRun)
                .where(IngestionRun.target_table == "market_data.t_assets")
                .order_by(IngestionRun.run_id.desc())
            ).scalars().first()
            assert audit_row.status == "success"
            assert audit_row.rows_written > 0

        with get_session() as session:
            new_run_ids = (
                set(
                    session.execute(
                        select(IngestionRun.run_id).where(
                            IngestionRun.target_table == "market_data.t_assets"
                        )
                    ).scalars()
                )
                - run_ids_before
            )
            if new_run_ids:
                from sqlalchemy import delete

                session.execute(delete(IngestionRun).where(IngestionRun.run_id.in_(new_run_ids)))
