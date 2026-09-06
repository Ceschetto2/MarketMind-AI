"""Test di integrazione per `db/writer.py` — richiedono Postgres reale.

`resolve_asset_id`/`upsert_market_price` usano la fixture `db_session`
(rollback automatico a fine test, anche sui `commit()` interni). `ingestion_run`
gestisce da sé le proprie sessioni (`get_session()`, non quella della
fixture) — per queste due scrive per davvero e ripulisce esplicitamente a
fine test, stesso principio delle verifiche manuali già fatte durante lo
sviluppo del modulo.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from marketmind_ai.db.models.audit import IngestionRun
from marketmind_ai.db.models.market_data import Asset, MarketPrice
from marketmind_ai.db.session import get_session
from marketmind_ai.db.writer import (
    AssetNotFoundError,
    ingestion_run,
    resolve_asset_id,
    upsert_market_price,
)
from marketmind_ai.schemas import MarketPriceRecord

pytestmark = pytest.mark.integration

TEST_ASSET_ID = -1  # id negativo: fuori dal range delle sequenze reali (Identity parte da 1)


def _make_asset(session, symbol: str = "TESTX") -> int:
    asset = Asset(
        asset_id=TEST_ASSET_ID,
        symbol=symbol,
        name="Test Asset",
        sector="Test",
        asset_type="equity",
        source="yfinance",
        fetched_at=datetime.now(timezone.utc),
    )
    session.add(asset)
    session.flush()
    return asset.asset_id


def _price_record(symbol: str, **overrides) -> MarketPriceRecord:
    defaults = dict(
        symbol=symbol,
        ts=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000,
        source="yfinance",
        fetched_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return MarketPriceRecord(**defaults)


class TestResolveAssetId:
    def test_trova_asset_esistente(self, db_session):
        asset_id = _make_asset(db_session, symbol="TESTX")
        assert resolve_asset_id(db_session, "TESTX") == asset_id

    def test_solleva_se_asset_non_esiste(self, db_session):
        with pytest.raises(AssetNotFoundError):
            resolve_asset_id(db_session, "SYMBOL_INESISTENTE")


class TestUpsertMarketPrice:
    def test_insert_prima_barra(self, db_session):
        asset_id = _make_asset(db_session)
        record = _price_record("TESTX")

        upsert_market_price(db_session, asset_id, record)
        db_session.flush()

        row = db_session.execute(
            select(MarketPrice).where(MarketPrice.asset_id == asset_id)
        ).scalar_one()
        assert row.close == 100.5
        assert row.volume == 1000

    def test_upsert_aggiorna_non_duplica(self, db_session):
        asset_id = _make_asset(db_session)
        upsert_market_price(db_session, asset_id, _price_record("TESTX", close=100.5))
        db_session.flush()

        # stessa chiave (asset_id, ts, source), prezzo diverso: deve
        # aggiornare la riga esistente, non inserirne una seconda.
        upsert_market_price(db_session, asset_id, _price_record("TESTX", close=200.0))
        db_session.flush()

        rows = (
            db_session.execute(
                select(MarketPrice).where(MarketPrice.asset_id == asset_id)
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].close == 200.0


class TestIngestionRun:
    """`ingestion_run` apre sessioni proprie (`get_session()`), indipendenti
    dalla fixture `db_session`: scrive per davvero, va ripulita a mano.
    """

    def _cleanup(self, run_id: int) -> None:
        with get_session() as session:
            session.execute(delete(IngestionRun).where(IngestionRun.run_id == run_id))

    def test_successo_traccia_rows_written(self):
        with ingestion_run("yfinance", "market_data.t_market_prices") as run:
            run.rows_written = 3
        run_id = run.run_id

        try:
            with get_session() as session:
                row = session.get(IngestionRun, run_id)
                assert row.status == "success"
                assert row.rows_written == 3
                assert row.finished_at is not None
        finally:
            self._cleanup(run_id)

    def test_fallimento_traccia_errore_e_rilancia(self):
        run_id = None
        with pytest.raises(ValueError, match="errore di prova"):
            with ingestion_run("yfinance", "market_data.t_market_prices") as run:
                run_id = run.run_id
                run.rows_written = 1
                raise ValueError("errore di prova")

        try:
            with get_session() as session:
                row = session.get(IngestionRun, run_id)
                assert row.status == "failed"
                assert row.rows_written == 1
                assert "errore di prova" in row.error_message
        finally:
            self._cleanup(run_id)
