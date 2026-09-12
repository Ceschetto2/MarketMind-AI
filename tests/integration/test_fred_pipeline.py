"""Test di integrazione per `run()` di `fred_pipeline.py`.

FRED è un servizio pubblico gratuito senza vincoli stretti di rate limit —
qui si usa una vera chiamata all'API reale con la chiave in `.env` invece
di mockare la rete, a differenza delle altre pipeline. `run()` apre le
proprie sessioni via `get_session()`, indipendenti dalla fixture
`db_session`: pulizia esplicita a fine test, stesso principio delle altre
pipeline.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from marketmind_ai.db.models.audit import IngestionRun
from marketmind_ai.db.models.market_data import MacroEvent
from marketmind_ai.db.session import get_session
from marketmind_ai.ingestion.fred_pipeline import INDICATORS, run

pytestmark = pytest.mark.integration


class TestRunEndToEnd:
    def _snapshot_existing(self) -> dict[str, set]:
        with get_session() as session:
            return {
                indicator: set(
                    session.execute(
                        select(MacroEvent.ts).where(MacroEvent.indicator == indicator)
                    ).scalars()
                )
                for indicator in INDICATORS
            }

    def test_run_scrive_osservazioni_reali(self):
        before = self._snapshot_existing()

        with get_session() as session:
            run_ids_before = set(
                session.execute(
                    select(IngestionRun.run_id).where(
                        IngestionRun.target_table == "market_data.t_macro_events"
                    )
                ).scalars()
            )

        run()

        with get_session() as session:
            # almeno un indicatore deve aver ricevuto almeno un'osservazione
            # nella finestra di lookback — non testiamo un valore esatto
            # (i dati reali cambiano), solo che la chiamata reale abbia
            # scritto qualcosa di plausibile.
            total_rows = sum(
                len(
                    session.execute(
                        select(MacroEvent.ts).where(MacroEvent.indicator == indicator)
                    ).scalars().all()
                )
                for indicator in INDICATORS
            )
            assert total_rows > 0

            audit_row = session.execute(
                select(IngestionRun)
                .where(IngestionRun.target_table == "market_data.t_macro_events")
                .order_by(IngestionRun.run_id.desc())
            ).scalars().first()
            assert audit_row.status == "success"

        # pulizia: rimuove solo le osservazioni scritte da questo test
        # (nuove rispetto allo snapshot iniziale) e i soli run di audit
        # generati qui — non tocca dati reali eventualmente già presenti.
        with get_session() as session:
            after = {
                indicator: set(
                    session.execute(
                        select(MacroEvent.ts).where(MacroEvent.indicator == indicator)
                    ).scalars()
                )
                for indicator in INDICATORS
            }
            for indicator in INDICATORS:
                new_ts = after[indicator] - before[indicator]
                if new_ts:
                    session.execute(
                        delete(MacroEvent).where(
                            MacroEvent.indicator == indicator, MacroEvent.ts.in_(new_ts)
                        )
                    )

            new_run_ids = (
                set(
                    session.execute(
                        select(IngestionRun.run_id).where(
                            IngestionRun.target_table == "market_data.t_macro_events"
                        )
                    ).scalars()
                )
                - run_ids_before
            )
            if new_run_ids:
                session.execute(delete(IngestionRun).where(IngestionRun.run_id.in_(new_run_ids)))
