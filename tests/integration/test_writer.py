"""Test di integrazione per `db/writer.py` — richiedono Postgres reale.

`resolve_asset_id`/`upsert_market_price` usano la fixture `db_session`
(rollback automatico a fine test, anche sui `commit()` interni). `ingestion_run`
gestisce da sé le proprie sessioni (`get_session()`, non quella della
fixture) — per queste due scrive per davvero e ripulisce esplicitamente a
fine test, stesso principio delle verifiche manuali già fatte durante lo
sviluppo del modulo.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import delete, select

from marketmind_ai.db.models.audit import IngestionRun
from marketmind_ai.db.models.market_data import Asset, CompanyEvent, MarketPrice, NewsEvent, UniverseMember
from marketmind_ai.db.models.raw import CompanyEventRaw, NewsEventRaw
from marketmind_ai.db.session import get_session
from marketmind_ai.db.writer import (
    AssetNotFoundError,
    ingestion_run,
    resolve_asset_id,
    resolve_or_create_asset,
    upsert_market_price,
    upsert_universe_member,
    write_company_event,
    write_news_event,
)
from marketmind_ai.schemas import (
    CompanyEventRecord,
    MarketPriceRecord,
    NewsEventRecord,
    UniverseMemberRecord,
)

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


def _universe_record(symbol: str, **overrides) -> UniverseMemberRecord:
    defaults = dict(
        symbol=symbol,
        name="Test Asset",
        sector="Test",
        asset_type="equity",
        is_benchmark=False,
        source="universe-csv",
        fetched_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return UniverseMemberRecord(**defaults)


class TestResolveOrCreateAsset:
    def test_crea_asset_se_manca(self, db_session):
        record = _universe_record("TESTX")

        asset_id = resolve_or_create_asset(db_session, record)
        db_session.flush()

        row = db_session.get(Asset, asset_id)
        assert row.symbol == "TESTX"
        assert row.asset_type == "equity"
        assert row.source == "universe-csv"

    def test_riusa_asset_esistente_senza_duplicare(self, db_session):
        existing_id = _make_asset(db_session, symbol="TESTX")
        db_session.flush()

        record = _universe_record("TESTX", name="Nome diverso, non deve sovrascrivere")
        asset_id = resolve_or_create_asset(db_session, record)

        assert asset_id == existing_id
        # non tocca una riga già scritta da un'altra pipeline (es.
        # yfinance-assets): resolve_or_create_asset crea solo se manca, non
        # aggiorna un asset già esistente.
        row = db_session.get(Asset, asset_id)
        assert row.name == "Test Asset"


class TestUpsertUniverseMember:
    def test_insert_e_upsert_idempotente(self, db_session):
        asset_id = _make_asset(db_session, symbol="TESTX")
        db_session.flush()

        upsert_universe_member(db_session, asset_id, _universe_record("TESTX", is_benchmark=False))
        db_session.flush()
        upsert_universe_member(db_session, asset_id, _universe_record("TESTX", is_benchmark=True))
        db_session.flush()

        rows = (
            db_session.execute(
                select(UniverseMember).where(UniverseMember.asset_id == asset_id)
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].is_benchmark is True


def _news_record(**overrides) -> NewsEventRecord:
    defaults = dict(
        source="Finnhub",
        ts=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        symbol=None,
        headline="Titolo di prova",
        raw_payload={"raw": "payload"},
        sentiment_score=None,
        url="https://example.com/articolo-di-prova",
        fetched_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return NewsEventRecord(**defaults)


def _company_record(**overrides) -> CompanyEventRecord:
    defaults = dict(
        symbol="TESTX",
        ts=date(2026, 1, 1),
        event_type="earnings",
        raw_payload={"raw": "payload"},
        source="Finnhub",
        fetched_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return CompanyEventRecord(**defaults)


class TestWriteNewsEvent:
    def test_insert_scrive_raffinata_e_raw(self, db_session):
        write_news_event(db_session, None, _news_record())
        db_session.flush()

        refined = db_session.execute(
            select(NewsEvent).where(NewsEvent.url == "https://example.com/articolo-di-prova")
        ).scalar_one()
        assert refined.headline == "Titolo di prova"
        assert refined.asset_id is None

        raw = db_session.get(NewsEventRaw, refined.news_event_id)
        assert raw.raw_payload == {"raw": "payload"}

    def test_upsert_su_stesso_url_aggiorna_non_duplica(self, db_session):
        write_news_event(db_session, None, _news_record(headline="Prima versione"))
        db_session.flush()
        write_news_event(
            db_session,
            None,
            _news_record(headline="Versione aggiornata", raw_payload={"v": 2}),
        )
        db_session.flush()

        rows = db_session.execute(
            select(NewsEvent).where(NewsEvent.url == "https://example.com/articolo-di-prova")
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].headline == "Versione aggiornata"

        raw = db_session.get(NewsEventRaw, rows[0].news_event_id)
        assert raw.raw_payload == {"v": 2}


class TestWriteCompanyEvent:
    def test_insert_scrive_raffinata_e_raw(self, db_session):
        asset_id = _make_asset(db_session, symbol="TESTX")
        db_session.flush()

        write_company_event(db_session, asset_id, _company_record())
        db_session.flush()

        refined = db_session.execute(
            select(CompanyEvent).where(CompanyEvent.asset_id == asset_id)
        ).scalar_one()
        assert refined.event_type == "earnings"

        raw = db_session.get(CompanyEventRaw, refined.company_event_id)
        assert raw.raw_payload == {"raw": "payload"}

    def test_upsert_su_stessa_chiave_naturale_aggiorna_non_duplica(self, db_session):
        asset_id = _make_asset(db_session, symbol="TESTX")
        db_session.flush()

        write_company_event(db_session, asset_id, _company_record(source="Finnhub"))
        db_session.flush()
        write_company_event(
            db_session, asset_id, _company_record(source="FMP", raw_payload={"v": 2})
        )
        db_session.flush()

        rows = db_session.execute(
            select(CompanyEvent).where(CompanyEvent.asset_id == asset_id)
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].source == "FMP"


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
