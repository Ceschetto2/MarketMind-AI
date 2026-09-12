"""Test di integrazione per `run()` di `finnhub_news_pipeline.py`.

Rete mockata (chiamare Finnhub per davvero non è deterministico: gli
articoli restituiti cambiano di continuo), DB reale — `run()` apre le
proprie sessioni via `get_session()`, indipendenti dalla fixture
`db_session`: setup/pulizia espliciti, stesso principio già in uso per
`yfinance-prices`/`universe-csv`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from marketmind_ai.db.models.audit import IngestionRun
from marketmind_ai.db.models.market_data import Asset, NewsEvent, UniverseMember
from marketmind_ai.db.models.raw import NewsEventRaw
from marketmind_ai.db.session import get_session
from marketmind_ai.ingestion.finnhub_news_pipeline import run

pytestmark = pytest.mark.integration

TEST_ASSET_ID = -3


def _fake_article(**overrides) -> dict:
    article = {
        "category": "company",
        "datetime": 1_756_339_200,
        "headline": "Titolo di prova",
        "id": 999,
        "image": "",
        "related": "TESTX",
        "source": "Reuters",
        "summary": "Sommario di prova.",
        "url": "https://example.com/finnhub-test-article",
    }
    article.update(overrides)
    return article


class TestRunEndToEnd:
    def _cleanup(self) -> None:
        with get_session() as session:
            news_ids = session.execute(
                select(NewsEvent.news_event_id).where(NewsEvent.asset_id == TEST_ASSET_ID)
            ).scalars().all()
            if news_ids:
                session.execute(
                    delete(NewsEventRaw).where(NewsEventRaw.news_event_id.in_(news_ids))
                )
                session.execute(delete(NewsEvent).where(NewsEvent.asset_id == TEST_ASSET_ID))
            session.execute(delete(UniverseMember).where(UniverseMember.asset_id == TEST_ASSET_ID))
            session.execute(delete(Asset).where(Asset.asset_id == TEST_ASSET_ID))
            session.execute(
                delete(IngestionRun).where(IngestionRun.target_table == "market_data.t_news_events")
            )

    def test_run_scrive_raffinata_e_raw(self, mocker):
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
            "marketmind_ai.ingestion.finnhub_news_pipeline.get_universe_symbols",
            return_value=["TESTX"],
        )
        mocker.patch("marketmind_ai.ingestion.finnhub_news_pipeline.time.sleep")
        mocker.patch(
            "marketmind_ai.ingestion.finnhub_news_pipeline._fetch_company_news",
            return_value=[_fake_article()],
        )

        try:
            run()

            with get_session() as session:
                refined = session.execute(
                    select(NewsEvent).where(NewsEvent.asset_id == TEST_ASSET_ID)
                ).scalar_one()
                assert refined.headline == "Titolo di prova"
                assert refined.source == "Finnhub"

                raw = session.get(NewsEventRaw, refined.news_event_id)
                assert raw.raw_payload["id"] == 999

                audit_row = session.execute(
                    select(IngestionRun)
                    .where(IngestionRun.target_table == "market_data.t_news_events")
                    .order_by(IngestionRun.run_id.desc())
                ).scalars().first()
                assert audit_row.status == "success"
                assert audit_row.rows_written == 1
        finally:
            self._cleanup()
