"""Test di integrazione dei sink e dei lookup dell'universo
(`marketmind_pipelines.sinks`, `marketmind_pipelines.lookups`).

Riprendono i casi del vecchio `test_writer.py` (upsert idempotente, payload
grezzo nella stessa transazione, fonti diverse sullo stesso evento che
convivono) più le colonne identificative FMP di `0008`. Tutto gira nella
transazione annullata di `rollback_db`: i simboli sintetici `TESTSINK*` non
sopravvivono al test, nemmeno se fallisce a metà.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from marketmind_db.database import Database
from marketmind_db.models.market_data import (
    Asset,
    CompanyEvent,
    MacroEvent,
    MarketPrice,
    NewsEvent,
    UniverseMember,
)
from marketmind_db.models.raw import CompanyEventRaw, NewsEventRaw
from marketmind_pipelines.lookups import (
    symbols_with_recent_company_events,
    universe_assets,
    universe_symbols,
)
from marketmind_pipelines.records import (
    AssetRecord,
    CompanyEventRecord,
    MacroEventRecord,
    MarketPriceRecord,
    NewsEventRecord,
    UniverseMemberRecord,
)
from marketmind_pipelines.sinks import (
    AssetNotFoundError,
    AssetSink,
    CompanyEventSink,
    MacroEventSink,
    MarketPriceSink,
    NewsEventSink,
    UniverseMemberSink,
    resolve_asset_ids,
)

pytestmark = pytest.mark.integration

_NOW = datetime.now(timezone.utc).replace(microsecond=0)
_SYMBOL = "TESTSINK"


def _member(symbol: str = _SYMBOL, *, is_benchmark: bool = False, name: str = "Test Sink Inc.") -> UniverseMemberRecord:
    return UniverseMemberRecord(
        symbol=symbol,
        name=name,
        sector="Test",
        asset_type="equity",
        is_benchmark=is_benchmark,
        source="universe-csv",
        fetched_at=_NOW,
    )


@pytest.fixture
def db(rollback_db: Database) -> Database:
    with rollback_db.transaction() as tx:
        UniverseMemberSink(tx).write([_member()])
    return rollback_db


def _company_event(**overrides) -> CompanyEventRecord:
    fields = {
        "symbol": _SYMBOL,
        "ts": date(2026, 6, 30),
        "event_type": "income_statement",
        "raw_payload": {"revenue": 1},
        "source": "FMP",
        "fetched_at": _NOW,
    }
    return CompanyEventRecord(**(fields | overrides))


class TestUniverseMemberSink:
    def test_crea_asset_e_membership(self, db):
        with db.transaction() as tx:
            asset = tx.repository(Asset).get_one(symbol=_SYMBOL)
            member = tx.repository(UniverseMember).get_one(asset_id=asset.asset_id)

        assert asset.name == "Test Sink Inc."
        assert member.is_benchmark is False

    def test_non_sovrascrive_lanagrafica_di_un_asset_esistente(self, db):
        """Un nome più fresco scritto da `yfinance-assets` non va perso a un
        nuovo giro di `universe-csv`; la membership invece si aggiorna."""
        with db.transaction() as tx:
            UniverseMemberSink(tx).write([_member(name="Nome dal CSV", is_benchmark=True)])
            asset = tx.repository(Asset).get_one(symbol=_SYMBOL)
            member = tx.repository(UniverseMember).get_one(asset_id=asset.asset_id)

        assert asset.name == "Test Sink Inc."
        assert member.is_benchmark is True


class TestAssetSink:
    def test_upsert_aggiorna_lanagrafica(self, db):
        record = AssetRecord(
            symbol=_SYMBOL, name="Nuovo nome", sector="Tech", asset_type="equity",
            source="yfinance", fetched_at=_NOW,
        )
        with db.transaction() as tx:
            assert AssetSink(tx).write([record]) == 1
            asset = tx.repository(Asset).get_one(symbol=_SYMBOL)

        assert (asset.name, asset.sector) == ("Nuovo nome", "Tech")


class TestResolveAssetIds:
    def test_simbolo_sconosciuto_solleva(self, db):
        with db.transaction() as tx:
            with pytest.raises(AssetNotFoundError, match="NON-ESISTE"):
                resolve_asset_ids(tx, ["NON-ESISTE", _SYMBOL])

    def test_risolve_tutti(self, db):
        with db.transaction() as tx:
            ids = resolve_asset_ids(tx, [_SYMBOL, _SYMBOL])

        assert set(ids) == {_SYMBOL}


class TestMarketPriceSink:
    def test_upsert_aggiorna_non_duplica(self, db):
        def bar(close: float) -> MarketPriceRecord:
            return MarketPriceRecord(
                symbol=_SYMBOL, ts=_NOW, open=1, high=2, low=0.5, close=close,
                volume=10, source="yfinance", fetched_at=_NOW,
            )

        with db.transaction() as tx:
            assert MarketPriceSink(tx).write([bar(1.0)]) == 1
            assert MarketPriceSink(tx).write([bar(2.0)]) == 1
            asset_id = resolve_asset_ids(tx, [_SYMBOL])[_SYMBOL]
            rows = tx.repository(MarketPrice).select(where={"asset_id": asset_id})

        assert [r.close for r in rows] == [2.0]


class TestMacroEventSink:
    def test_valore_rivisto_sovrascrive(self, db):
        def obs(value: float | None) -> MacroEventRecord:
            return MacroEventRecord(
                indicator="TESTSINK_IND", ts=date(2026, 9, 1), value=value,
                source="FRED", fetched_at=_NOW,
            )

        with db.transaction() as tx:
            MacroEventSink(tx).write([obs(None)])
            MacroEventSink(tx).write([obs(4.2)])
            row = tx.repository(MacroEvent).get_one(indicator="TESTSINK_IND", ts=date(2026, 9, 1))

        assert row.value == 4.2


class TestNewsEventSink:
    def test_raffinata_e_raw_nella_stessa_transazione(self, db):
        url = "https://example.test/testsink/1"

        def article(headline: str, payload: dict) -> NewsEventRecord:
            return NewsEventRecord(
                source="Finnhub", ts=_NOW, symbol=_SYMBOL, headline=headline,
                raw_payload=payload, url=url, fetched_at=_NOW,
            )

        with db.transaction() as tx:
            assert NewsEventSink(tx).write([article("v1", {"v": 1})]) == 1
            NewsEventSink(tx).write([article("v2", {"v": 2}), article("v3", {"v": 3})])
            news = tx.repository(NewsEvent).select(where={"url": url})
            raw = tx.repository(NewsEventRaw).get_one(news_event_id=news[0].news_event_id)

        assert [n.headline for n in news] == ["v3"]
        assert raw.raw_payload == {"v": 3}

    def test_news_senza_simbolo_scritta_senza_asset(self, db):
        url = "https://example.test/testsink/unlinked"
        record = NewsEventRecord(
            source="GDELT-ngrams", ts=_NOW, symbol=None, headline="h",
            raw_payload={}, url=url, fetched_at=_NOW,
        )
        with db.transaction() as tx:
            NewsEventSink(tx).write([record])
            [news] = tx.repository(NewsEvent).select(where={"url": url})

        assert news.asset_id is None


class TestCompanyEventSink:
    def test_colonne_identificative_e_raw(self, db):
        record = _company_event(
            fiscal_year="2026", period="Q2", reported_currency="USD", cik="0000000001",
            filing_date=date(2026, 8, 1), accepted_date=date(2026, 8, 2),
        )
        with db.transaction() as tx:
            assert CompanyEventSink(tx).write([record]) == 1
            asset_id = resolve_asset_ids(tx, [_SYMBOL])[_SYMBOL]
            [event] = tx.repository(CompanyEvent).select(where={"asset_id": asset_id})
            raw = tx.repository(CompanyEventRaw).get_one(company_event_id=event.company_event_id)

        assert event.event_type == "income_statement"
        assert (event.fiscal_year, event.period, event.reported_currency) == ("2026", "Q2", "USD")
        assert (event.filing_date, event.accepted_date) == (date(2026, 8, 1), date(2026, 8, 2))
        assert raw.raw_payload == {"revenue": 1}

    def test_tre_bilanci_stessa_data_convivono(self, db):
        """Prima di `0009` i tre bilanci FMP erano tutti `earnings` e
        collidevano sulla stessa chiave: ne sopravviveva uno solo."""
        records = [
            _company_event(event_type=t) for t in ("income_statement", "balance_sheet", "cash_flow")
        ]
        with db.transaction() as tx:
            CompanyEventSink(tx).write(records)
            asset_id = resolve_asset_ids(tx, [_SYMBOL])[_SYMBOL]
            types = tx.repository(CompanyEvent).values("event_type", where={"asset_id": asset_id})

        assert sorted(types) == ["balance_sheet", "cash_flow", "income_statement"]

    def test_fonti_diverse_stesso_evento_convivono_e_upsert_non_duplica(self, db):
        earnings = _company_event(event_type="earnings", source="Finnhub")
        with db.transaction() as tx:
            CompanyEventSink(tx).write([earnings, _company_event(event_type="earnings")])
            CompanyEventSink(tx).write([earnings.model_copy(update={"raw_payload": {"eps": 2}})])
            asset_id = resolve_asset_ids(tx, [_SYMBOL])[_SYMBOL]
            sources = tx.repository(CompanyEvent).values("source", where={"asset_id": asset_id})

        assert sorted(sources) == ["FMP", "Finnhub"]


class TestLookups:
    def test_universo_esclude_asset_fuori_universo(self, db):
        with db.transaction() as tx:
            AssetSink(tx).write(
                [AssetRecord(symbol="TESTSINK_OUT", name="Fuori", asset_type="equity", source="yfinance", fetched_at=_NOW)]
            )
            symbols = universe_symbols(tx)
            assets = dict(universe_assets(tx))

        assert _SYMBOL in symbols
        assert "TESTSINK_OUT" not in symbols
        assert assets[_SYMBOL] == "Test Sink Inc."

    def test_simboli_con_eventi_recenti(self, db):
        with db.transaction() as tx:
            CompanyEventSink(tx).write(
                [
                    _company_event(event_type="earnings", source="Finnhub", ts=date.today() - timedelta(days=1)),
                ]
            )
            recent = symbols_with_recent_company_events(
                tx, source="Finnhub", event_type="earnings", since=date.today() - timedelta(days=14)
            )
            none_recent = symbols_with_recent_company_events(
                tx, source="Finnhub", event_type="earnings", since=date.today() + timedelta(days=1)
            )

        assert _SYMBOL in recent
        assert _SYMBOL not in none_recent
