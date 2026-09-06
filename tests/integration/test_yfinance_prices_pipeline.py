"""Test di integrazione per `ingestion/yfinance_prices_pipeline.py`.

`get_universe_symbols()` usa la fixture `db_session` (rollback automatico).
`test_run_end_to_end` esercita `run()` per intero — che apre le proprie
sessioni (`get_session()`, indipendenti dalla fixture) — con la sola rete
(`.history()`) mockata: stessa verifica end-to-end già fatta a mano durante
lo sviluppo (asset di prova reale, run vero, pulizia esplicita a fine test),
qui automatizzata.
"""

from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import delete, select

from marketmind_ai.db.models.audit import IngestionRun
from marketmind_ai.db.models.market_data import Asset, MarketPrice, UniverseMember
from marketmind_ai.db.session import get_session
from marketmind_ai.ingestion.yfinance_prices_pipeline import get_universe_symbols, run

pytestmark = pytest.mark.integration

TEST_ASSET_ID = -2


def _fake_history() -> pd.DataFrame:
    index = pd.date_range("2026-01-01 09:30", periods=2, freq="60min", tz="UTC")
    return pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.5, 102.5],
            "Volume": [1000, 1200],
        },
        index=index,
    )


class TestGetUniverseSymbols:
    """`get_universe_symbols()` apre una propria sessione (`get_session()`),
    su una connessione diversa da quella di `db_session` (che tiene aperta
    una transazione con savepoint su una connessione a sé): righe scritte
    tramite `db_session` non sarebbero visibili a un'altra connessione
    finché non committate per davvero, per il normale isolamento delle
    transazioni Postgres. Setup/pulizia qui passano quindi anche loro da
    `get_session()`, non dalla fixture — stesso principio di `TestRunEndToEnd`.
    """

    def _cleanup(self) -> None:
        with get_session() as session:
            session.execute(
                delete(UniverseMember).where(
                    UniverseMember.asset_id.in_([TEST_ASSET_ID, TEST_ASSET_ID - 1])
                )
            )
            session.execute(
                delete(Asset).where(
                    Asset.asset_id.in_([TEST_ASSET_ID, TEST_ASSET_ID - 1])
                )
            )

    def test_include_solo_membri_universo(self):
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
                Asset(
                    asset_id=TEST_ASSET_ID - 1,
                    symbol="TESTY_NON_UNIVERSO",
                    name="Non in universo",
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

        try:
            symbols = get_universe_symbols()
            assert "TESTX" in symbols
            assert "TESTY_NON_UNIVERSO" not in symbols
        finally:
            self._cleanup()


class TestRunEndToEnd:
    """`run()` scrive per davvero (le sue sessioni non passano dalla fixture
    `db_session`): setup/pulizia espliciti, come le verifiche manuali già
    fatte durante lo sviluppo di questa pipeline.

    `get_universe_symbols()` è mockata per restituire solo il ticker di
    prova: senza, il test spazzolerebbe anche l'universo reale (popolato
    dalla pipeline `universe-csv`, non vuoto come quando questo test è
    stato scritto) — un test non deve dipendere da cosa contiene per caso
    l'ambiente in cui gira.
    """

    def _cleanup(self, run_ids_before: set[int]) -> None:
        with get_session() as session:
            session.execute(
                delete(MarketPrice).where(MarketPrice.asset_id == TEST_ASSET_ID)
            )
            session.execute(
                delete(UniverseMember).where(UniverseMember.asset_id == TEST_ASSET_ID)
            )
            session.execute(delete(Asset).where(Asset.asset_id == TEST_ASSET_ID))
            # solo le righe di audit create da *questo* test, non l'intera
            # storia di t_ingestion_runs per questa target_table (ci sono
            # anche run reali, non di test, da non toccare).
            new_run_ids = (
                set(
                    session.execute(
                        select(IngestionRun.run_id).where(
                            IngestionRun.target_table == "market_data.t_market_prices"
                        )
                    ).scalars()
                )
                - run_ids_before
            )
            if new_run_ids:
                session.execute(
                    delete(IngestionRun).where(IngestionRun.run_id.in_(new_run_ids))
                )

    def test_run_end_to_end(self, mocker):
        mocker.patch(
            "marketmind_ai.ingestion.yfinance_prices_pipeline.get_universe_symbols",
            return_value=["TESTX"],
        )

        with get_session() as session:
            run_ids_before = set(
                session.execute(
                    select(IngestionRun.run_id).where(
                        IngestionRun.target_table == "market_data.t_market_prices"
                    )
                ).scalars()
            )

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
            "marketmind_ai.ingestion.yfinance_prices_pipeline._fetch_history",
            return_value=_fake_history(),
        )
        # niente attesa reale tra ticker durante il test.
        mocker.patch("marketmind_ai.ingestion.yfinance_prices_pipeline.time.sleep")

        try:
            run()

            with get_session() as session:
                rows = (
                    session.execute(
                        select(MarketPrice).where(
                            MarketPrice.asset_id == TEST_ASSET_ID
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(rows) == 2
                assert {row.close for row in rows} == {101.5, 102.5}

                audit_row = session.execute(
                    select(IngestionRun)
                    .where(
                        IngestionRun.target_table == "market_data.t_market_prices"
                    )
                    .order_by(IngestionRun.run_id.desc())
                ).scalars().first()
                assert audit_row.status == "success"
                assert audit_row.rows_written == 2
        finally:
            self._cleanup(run_ids_before)
