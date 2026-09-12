"""Test di integrazione per `run()` di `fmp_pipeline.py`.

Vera chiamata all'API FMP reale (chiave in `.env`) per l'asset AAPL, già
presente in `t_assets` (scritto da `universe-csv`) — nessun mock di rete,
a differenza delle pipeline con volume/non-determinismo più alto. Simula
l'output di `finnhub-earnings` (da cui questa pipeline dipende in
produzione via `OnSuccess=`) scrivendo direttamente un `CompanyEvent` di
prova con `source='Finnhub'`, senza dover eseguire quella pipeline.

`run()` apre le proprie sessioni via `get_session()`: pulizia esplicita a
fine test, per non lasciare né la riga Finnhub di prova né le righe FMP
scritte dalla vera chiamata.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from marketmind_ai.db.models.audit import IngestionRun
from marketmind_ai.db.models.market_data import Asset, CompanyEvent
from marketmind_ai.db.models.raw import CompanyEventRaw
from marketmind_ai.db.session import get_session
from marketmind_ai.ingestion.fmp_pipeline import run

pytestmark = pytest.mark.integration

_SYMBOL = "AAPL"  # già in t_assets (seeds/universe.csv), asset reale


class TestRunEndToEnd:
    def test_run_scrive_fondamentali_reali_per_asset_con_earnings_recente(self):
        with get_session() as session:
            asset_id = session.execute(
                select(Asset.asset_id).where(Asset.symbol == _SYMBOL)
            ).scalar_one()

            # simula l'output di finnhub-earnings, senza eseguirla davvero.
            finnhub_earnings_row = CompanyEvent(
                asset_id=asset_id,
                ts=date.today() - timedelta(days=1),
                event_type="earnings",
                source="Finnhub",
                fetched_at=datetime.now(timezone.utc),
            )
            session.add(finnhub_earnings_row)
            session.flush()
            finnhub_event_id = finnhub_earnings_row.company_event_id

            existing_fmp_ts = set(
                session.execute(
                    select(CompanyEvent.ts).where(
                        CompanyEvent.asset_id == asset_id, CompanyEvent.source == "FMP"
                    )
                ).scalars()
            )
            run_ids_before = set(
                session.execute(
                    select(IngestionRun.run_id).where(
                        IngestionRun.target_table == "market_data.t_company_events"
                    )
                ).scalars()
            )

        try:
            run()

            with get_session() as session:
                fmp_rows = session.execute(
                    select(CompanyEvent).where(
                        CompanyEvent.asset_id == asset_id, CompanyEvent.source == "FMP"
                    )
                ).scalars().all()
                # almeno uno dei 5 endpoint deve aver restituito qualcosa di
                # scrivibile per un ticker reale come AAPL.
                assert len(fmp_rows) > 0
                assert {row.event_type for row in fmp_rows} <= {
                    "income_statement",
                    "balance_sheet",
                    "cash_flow",
                    "dividend",
                    "split",
                }

                audit_row = session.execute(
                    select(IngestionRun)
                    .where(IngestionRun.target_table == "market_data.t_company_events")
                    .order_by(IngestionRun.run_id.desc())
                ).scalars().first()
                assert audit_row.status == "success"
        finally:
            with get_session() as session:
                fmp_rows = session.execute(
                    select(CompanyEvent).where(
                        CompanyEvent.asset_id == asset_id, CompanyEvent.source == "FMP"
                    )
                ).scalars().all()
                for row in fmp_rows:
                    if row.ts not in existing_fmp_ts:
                        session.execute(
                            CompanyEventRaw.__table__.delete().where(
                                CompanyEventRaw.company_event_id == row.company_event_id
                            )
                        )
                        session.delete(row)

                session.execute(
                    CompanyEventRaw.__table__.delete().where(
                        CompanyEventRaw.company_event_id == finnhub_event_id
                    )
                )
                finnhub_row = session.get(CompanyEvent, finnhub_event_id)
                if finnhub_row is not None:
                    session.delete(finnhub_row)

                new_run_ids = (
                    set(
                        session.execute(
                            select(IngestionRun.run_id).where(
                                IngestionRun.target_table == "market_data.t_company_events"
                            )
                        ).scalars()
                    )
                    - run_ids_before
                )
                if new_run_ids:
                    session.execute(
                        IngestionRun.__table__.delete().where(
                            IngestionRun.run_id.in_(new_run_ids)
                        )
                    )
