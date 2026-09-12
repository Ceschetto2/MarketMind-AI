"""Test di integrazione per `run()` di `gdelt_ngrams_pipeline.py`.

I file GDELT reali contengono milioni di righe e cambiano ogni minuto —
qui `_download_gz` è mockato con contenuto gzip fittizio ma nel formato
reale (poche righe), DB vero. `run()` apre le proprie sessioni via
`get_session()`: pulizia esplicita a fine test.
"""

from __future__ import annotations

import gzip
import json

import pytest
from sqlalchemy import delete, select

from marketmind_ai.db.models.audit import IngestionRun
from marketmind_ai.db.models.market_data import Asset, NewsEvent, UniverseMember
from marketmind_ai.db.models.raw import NewsEventRaw
from marketmind_ai.db.session import get_session
from marketmind_ai.ingestion.gdelt_ngrams_pipeline import run

pytestmark = pytest.mark.integration

TEST_ASSET_ID = -5


def _gz(text: str) -> str:
    """`_download_gz` restituisce testo decompresso — nel test lo mockiamo
    direttamente a livello di funzione, non serve comprimere per davvero."""
    return text


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

    def test_run_linka_e_scrive_articolo_matchato(self, mocker):
        with get_session() as session:
            session.add(
                Asset(
                    asset_id=TEST_ASSET_ID,
                    symbol="ZZZCORP",
                    name="Zzzcorp Industries",
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

        ngrams_content = (
            "1\tZzzcorp reported strong results\t2\n"
            "2\tcompletely unrelated quadgram text\t1\n"
        )
        toc_content = "\n".join(
            [
                json.dumps(
                    {
                        "ID": 1,
                        "date": "2026-09-12",
                        "title": "Zzzcorp posts record quarter",
                        "lang": "en",
                        "img": "",
                        "url": "https://example.com/zzzcorp-article",
                    }
                ),
                json.dumps(
                    {
                        "ID": 2,
                        "date": "2026-09-12",
                        "title": "Irrelevant",
                        "lang": "en",
                        "img": "",
                        "url": "https://example.com/other",
                    }
                ),
            ]
        )

        mocker.patch(
            "marketmind_ai.ingestion.gdelt_ngrams_pipeline._download_gz",
            side_effect=[_gz(ngrams_content), _gz(toc_content)],
        )

        try:
            run()

            with get_session() as session:
                refined = session.execute(
                    select(NewsEvent).where(NewsEvent.asset_id == TEST_ASSET_ID)
                ).scalar_one()
                assert refined.headline == "Zzzcorp posts record quarter"
                assert refined.source == "GDELT-ngrams"

                raw = session.get(NewsEventRaw, refined.news_event_id)
                assert raw.raw_payload["url"] == "https://example.com/zzzcorp-article"

                audit_row = session.execute(
                    select(IngestionRun)
                    .where(IngestionRun.target_table == "market_data.t_news_events")
                    .order_by(IngestionRun.run_id.desc())
                ).scalars().first()
                assert audit_row.status == "success"
                assert audit_row.rows_written == 1
        finally:
            self._cleanup()

    def test_run_nessun_file_pubblicato_traccia_comunque_lesecuzione(self, mocker):
        """`t_ingestion_runs` traccia ogni esecuzione (`db/01_schema_dati_er.md`),
        non solo quelle che trovano dati — altrimenti non c'è modo di
        distinguere "nessun file pubblicato in questo giro" (normale) da
        "la pipeline non gira mai" (un problema reale)."""
        with get_session() as session:
            run_ids_before = set(
                session.execute(
                    select(IngestionRun.run_id).where(
                        IngestionRun.target_table == "market_data.t_news_events"
                    )
                ).scalars()
            )

        mocker.patch(
            "marketmind_ai.ingestion.gdelt_ngrams_pipeline._download_gz",
            return_value=None,
        )

        try:
            run()  # non deve sollevare

            with get_session() as session:
                audit_row = session.execute(
                    select(IngestionRun)
                    .where(IngestionRun.target_table == "market_data.t_news_events")
                    .order_by(IngestionRun.run_id.desc())
                ).scalars().first()
                assert audit_row.status == "success"
                assert audit_row.rows_written == 0
        finally:
            with get_session() as session:
                new_run_ids = (
                    set(
                        session.execute(
                            select(IngestionRun.run_id).where(
                                IngestionRun.target_table == "market_data.t_news_events"
                            )
                        ).scalars()
                    )
                    - run_ids_before
                )
                if new_run_ids:
                    session.execute(
                        delete(IngestionRun).where(IngestionRun.run_id.in_(new_run_ids))
                    )
