"""Test di integrazione per `run()` di `finnhub_earnings_pipeline.py`.

Rete mockata (il calendario earnings reale non è deterministico), DB reale.
`run()` apre le proprie sessioni via `get_session()`: pulizia esplicita a
fine test, stesso principio delle altre pipeline.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from marketmind_ai.db.models.audit import IngestionRun
from marketmind_ai.db.models.market_data import Asset, CompanyEvent, UniverseMember
from marketmind_ai.db.models.raw import CompanyEventRaw
from marketmind_ai.db.session import get_session
from marketmind_ai.ingestion.finnhub_earnings_pipeline import run

pytestmark = pytest.mark.integration

TEST_ASSET_ID = -4


class TestRunEndToEnd:
    def _cleanup(self) -> None:
        with get_session() as session:
            event_ids = session.execute(
                select(CompanyEvent.company_event_id).where(
                    CompanyEvent.asset_id == TEST_ASSET_ID
                )
            ).scalars().all()
            if event_ids:
                session.execute(
                    delete(CompanyEventRaw).where(
                        CompanyEventRaw.company_event_id.in_(event_ids)
                    )
                )
                session.execute(
                    delete(CompanyEvent).where(CompanyEvent.asset_id == TEST_ASSET_ID)
                )
            session.execute(delete(UniverseMember).where(UniverseMember.asset_id == TEST_ASSET_ID))
            session.execute(delete(Asset).where(Asset.asset_id == TEST_ASSET_ID))
            session.execute(
                delete(IngestionRun).where(
                    IngestionRun.target_table == "market_data.t_company_events"
                )
            )

    def test_run_filtra_universo_e_scrive(self, mocker):
        with get_session() as session:
            session.add(
                Asset(
                    asset_id=TEST_ASSET_ID,
                    symbol="TESTX",
                    name="Test Asset",
                    sector="Test",
                    asset_type="equity",
                    source="yfinance",
                    fetched_at="2026-01-01T00:00:00+00:00",
                )
            )
            session.add(
                UniverseMember(
                    asset_id=TEST_ASSET_ID,
                    is_benchmark=False,
                    source="universe-csv",
                    fetched_at="2026-01-01T00:00:00+00:00",
                )
            )

        mocker.patch(
            "marketmind_ai.ingestion.finnhub_earnings_pipeline.get_api_key",
            return_value="fake-key",
        )
        mocker.patch(
            "marketmind_ai.ingestion.finnhub_earnings_pipeline.get_universe_symbols",
            return_value=["TESTX"],
        )
        mocker.patch(
            "marketmind_ai.ingestion.finnhub_earnings_pipeline._fetch_earnings_calendar",
            return_value=[
                {
                    "symbol": "TESTX",
                    "date": "2026-08-15",
                    "hour": "amc",
                    "quarter": 3,
                    "year": 2026,
                    "epsEstimate": 1.5,
                    "epsActual": 1.6,
                    "revenueEstimate": 90_000_000,
                    "revenueActual": 91_000_000,
                },
                {
                    "symbol": "NOTINUNIVERSE",
                    "date": "2026-08-15",
                    "hour": "amc",
                    "quarter": 3,
                    "year": 2026,
                    "epsEstimate": 1.0,
                    "epsActual": 1.0,
                    "revenueEstimate": 1,
                    "revenueActual": 1,
                },
            ],
        )

        try:
            run()

            with get_session() as session:
                refined = session.execute(
                    select(CompanyEvent).where(CompanyEvent.asset_id == TEST_ASSET_ID)
                ).scalar_one()
                assert refined.event_type == "earnings"
                assert refined.source == "Finnhub"

                raw = session.get(CompanyEventRaw, refined.company_event_id)
                assert raw.raw_payload["quarter"] == 3

                audit_row = session.execute(
                    select(IngestionRun)
                    .where(IngestionRun.target_table == "market_data.t_company_events")
                    .order_by(IngestionRun.run_id.desc())
                ).scalars().first()
                assert audit_row.status == "success"
                assert audit_row.rows_written == 1  # NOTINUNIVERSE escluso
        finally:
            self._cleanup()
