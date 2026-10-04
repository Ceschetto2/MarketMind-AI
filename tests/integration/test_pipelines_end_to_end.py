"""Test di integrazione end-to-end delle otto pipeline (`BasePipeline.run()`).

Ogni pipeline gira per intero — audit in `t_ingestion_runs`, lettura
dell'universo, sink, upsert reali su Postgres — dentro la transazione
sempre annullata di `rollback_db`: niente pulizia manuale, niente dati di
prova che sopravvivono a un test fallito a metà (il problema dei simboli
sintetici rimasti nell'universo e alimentati per giorni dai timer reali).

La rete è mockata dove il dato è volatile o la fonte è a rate limit
stretto (Finnhub, GDELT, prezzi yfinance); FRED, l'anagrafica yfinance e FMP
chiamano l'API reale, ma su un sottoinsieme minimo (FMP: solo AAPL, 5
richieste sulle 250/giorno del piano free).
"""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest
import requests

from marketmind_db.database import Database
from marketmind_db.models.audit import IngestionRun
from marketmind_db.models.market_data import (
    Asset,
    CompanyEvent,
    MacroEvent,
    MarketPrice,
    NewsEvent,
    UniverseMember,
)
from marketmind_db.models.raw import NewsEventRaw
from marketmind_pipelines.finnhub_earnings_pipeline import FinnhubEarningsPipeline
from marketmind_pipelines.finnhub_news_pipeline import FinnhubNewsPipeline
from marketmind_pipelines.fmp_pipeline import FmpPipeline
from marketmind_pipelines.fred_pipeline import INDICATORS, FredPipeline
from marketmind_pipelines.gdelt_ngrams_pipeline import GdeltNgramsPipeline
from marketmind_pipelines.http import HttpSource
from marketmind_pipelines.records import UniverseMemberRecord
from marketmind_pipelines.sinks import UniverseMemberSink, resolve_asset_ids
from marketmind_pipelines.universe_csv_pipeline import UniverseCsvPipeline
from marketmind_pipelines.yfinance_assets_pipeline import YFinanceAssetsPipeline
from marketmind_pipelines.yfinance_prices_pipeline import YFinancePricesPipeline

pytestmark = pytest.mark.integration

_NOW = datetime.now(timezone.utc).replace(microsecond=0)
_SYMBOL = "TESTPIPE"


@pytest.fixture
def db(rollback_db: Database) -> Database:
    """Universo di prova: un asset sintetico, dentro la transazione annullata."""
    with rollback_db.transaction() as tx:
        UniverseMemberSink(tx).write(
            [
                UniverseMemberRecord(
                    symbol=_SYMBOL, name="Testpipe Holdings", sector="Test", asset_type="equity",
                    is_benchmark=False, source="universe-csv", fetched_at=_NOW,
                )
            ]
        )
    return rollback_db


def _audit(db: Database, run_id: int) -> IngestionRun:
    with db.session() as session:
        return session.get(IngestionRun, run_id)


def _asset_id(db: Database, symbol: str = _SYMBOL) -> int:
    with db.transaction() as tx:
        return resolve_asset_ids(tx, [symbol])[symbol]


class TestUniverseCsv:
    def test_crea_asset_e_membership_ed_e_idempotente(self, rollback_db, tmp_path):
        csv_path = tmp_path / "universe.csv"
        csv_path.write_text(
            "symbol,name,sector,asset_type,is_benchmark\n"
            "TESTCSV1,Test Uno,Tech,equity,false\n"
            "TESTCSV2,Test Bench,,etf,true\n"
        )

        first = UniverseCsvPipeline(rollback_db, csv_path=csv_path).run()
        second = UniverseCsvPipeline(rollback_db, csv_path=csv_path).run()

        with rollback_db.transaction() as tx:
            ids = resolve_asset_ids(tx, ["TESTCSV1", "TESTCSV2"])
            members = tx.repository(UniverseMember).select(where={"asset_id": list(ids.values())})
        assert (first.status, first.rows_written, second.rows_written) == ("success", 2, 2)
        assert {m.asset_id: m.is_benchmark for m in members} == {ids["TESTCSV1"]: False, ids["TESTCSV2"]: True}
        assert _audit(rollback_db, first.run_id).status == "success"

    def test_csv_malformato_fa_fallire_il_run(self, rollback_db, tmp_path):
        csv_path = tmp_path / "universe.csv"
        csv_path.write_text("symbol,name,sector,asset_type,is_benchmark\n,Senza simbolo,,equity,false\n")

        with pytest.raises(ValueError):
            UniverseCsvPipeline(rollback_db, csv_path=csv_path).run()

        with rollback_db.session() as session:
            last = session.query(IngestionRun).order_by(IngestionRun.run_id.desc()).first()
        assert last.status == "failed"


class TestYFinancePrices:
    def test_scrive_le_barre_e_traccia_il_run(self, db, mocker):
        index = pd.date_range("2026-01-01 09:30", periods=2, freq="60min", tz="UTC")
        history = pd.DataFrame(
            {"Open": [100.0, 101.0], "High": [102.0, 103.0], "Low": [99.0, 100.0],
             "Close": [101.5, 102.5], "Volume": [1000, 1200]},
            index=index,
        )
        mocker.patch("marketmind_pipelines.yfinance_prices_pipeline.yf.Ticker").return_value.history.return_value = history

        result = YFinancePricesPipeline(db, symbols=[_SYMBOL], sleep=lambda _: None).run()

        with db.transaction() as tx:
            prices = tx.repository(MarketPrice).select(where={"asset_id": _asset_id(db)}, order_by=("ts",))
        assert [p.close for p in prices] == [101.5, 102.5]
        audit = _audit(db, result.run_id)
        assert (audit.status, audit.rows_written) == ("success", 2)

    def test_un_ticker_fallito_rende_il_run_partial(self, db, mocker):
        def history_for(symbol):
            ticker = mocker.Mock()
            if symbol == "AAPL":
                ticker.history.side_effect = RuntimeError("rate limit")
            else:
                ticker.history.return_value = pd.DataFrame(
                    {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]},
                    index=pd.DatetimeIndex([_NOW]),
                )
            return ticker

        mocker.patch("marketmind_pipelines.yfinance_prices_pipeline.yf.Ticker", side_effect=history_for)

        result = YFinancePricesPipeline(db, symbols=["AAPL", _SYMBOL], sleep=lambda _: None).run()

        audit = _audit(db, result.run_id)
        assert audit.status == "partial"
        assert "1/2" in audit.error_message and "AAPL" in audit.error_message
        assert audit.rows_written == 1


class TestYFinanceAssets:
    def test_aggiorna_lanagrafica_reale(self, rollback_db):
        """Vera chiamata a yfinance (nessuna chiave), su due ticker reali."""
        result = YFinanceAssetsPipeline(rollback_db, symbols=["AAPL", "SPY"], sleep=lambda _: None).run()

        with rollback_db.transaction() as tx:
            aapl = tx.repository(Asset).get_one(symbol="AAPL")
            spy = tx.repository(Asset).get_one(symbol="SPY")
        assert result.status == "success"
        assert aapl.name and aapl.asset_type == "equity"
        assert spy.asset_type == "etf"


class TestFinnhubNews:
    def test_scrive_raffinata_e_raw(self, db, mocker):
        http = mocker.MagicMock(spec=HttpSource)
        http.get_json.return_value = [
            {"datetime": int(_NOW.timestamp()), "headline": "Testpipe sale", "id": 1,
             "url": "https://example.test/testpipe/1", "source": "test"},
        ]

        result = FinnhubNewsPipeline(db, http=http, symbols=[_SYMBOL], sleep=lambda _: None).run()

        with db.transaction() as tx:
            [news] = tx.repository(NewsEvent).select(where={"url": "https://example.test/testpipe/1"})
            raw = tx.repository(NewsEventRaw).get_one(news_event_id=news.news_event_id)
        assert news.asset_id == _asset_id(db)
        assert raw.raw_payload["headline"] == "Testpipe sale"
        assert _audit(db, result.run_id).status == "success"


class TestFinnhubEarnings:
    def test_filtra_luniverso_e_scrive(self, db, mocker):
        http = mocker.MagicMock(spec=HttpSource)
        http.get_json.return_value = {
            "earningsCalendar": [
                {"symbol": _SYMBOL, "date": date.today().isoformat(), "epsEstimate": 1.0},
                {"symbol": "NON-IN-UNIVERSO", "date": date.today().isoformat()},
            ]
        }

        result = FinnhubEarningsPipeline(db, http=http).run()

        with db.transaction() as tx:
            events = tx.repository(CompanyEvent).select(where={"asset_id": _asset_id(db)})
        assert [(e.event_type, e.source) for e in events] == [("earnings", "Finnhub")]
        assert (result.status, result.rows_written) == ("success", 1)

    def test_api_giu_e_failed(self, db, mocker):
        http = mocker.MagicMock(spec=HttpSource)
        http.get_json.side_effect = requests.HTTPError("503")

        result = FinnhubEarningsPipeline(db, http=http).run()

        assert result.status == "failed"
        assert _audit(db, result.run_id).status == "failed"


class TestGdeltNgrams:
    def _response(self, text: str) -> requests.Response:
        response = requests.Response()
        response.status_code = 200
        response._content = gzip.compress(text.encode())
        return response

    def test_linka_e_scrive_larticolo(self, db, mocker):
        http = mocker.MagicMock(spec=HttpSource)
        toc = json.dumps({"ID": 7, "date": _NOW.strftime("%Y-%m-%dT%H:%M:%S"), "title": "Testpipe news",
                          "url": "https://example.test/testpipe/gdelt"})
        http.get.side_effect = [self._response("7\tTestpipe announced results\t1\n"), self._response(toc)]

        result = GdeltNgramsPipeline(db, http=http).run()

        with db.transaction() as tx:
            [news] = tx.repository(NewsEvent).select(where={"url": "https://example.test/testpipe/gdelt"})
        assert news.asset_id == _asset_id(db)
        assert news.source == "GDELT-ngrams"
        assert result.status == "success"

    def test_nessun_file_pubblicato_traccia_comunque_il_run(self, db, mocker):
        http = mocker.MagicMock(spec=HttpSource)
        http.get.return_value = None

        result = GdeltNgramsPipeline(db, http=http).run()

        audit = _audit(db, result.run_id)
        assert (audit.status, audit.rows_written) == ("success", 0)


class TestFred:
    def test_scrive_osservazioni_reali(self, rollback_db):
        """Vera chiamata ALFRED (chiave in `.env`), quattro indicatori."""
        result = FredPipeline(rollback_db).run()

        with rollback_db.transaction() as tx:
            written = tx.repository(MacroEvent).count(
                where=[
                    MacroEvent.indicator.in_(INDICATORS),
                    MacroEvent.fetched_at >= _NOW - timedelta(minutes=1),
                ]
            )
        assert result.status == "success"
        assert written > 0


class TestFmp:
    def test_fondamentali_reali_di_aapl(self, rollback_db):
        """Vera chiamata FMP (chiave in `.env`) su AAPL soltanto: 5 richieste.
        Un endpoint fuori dal piano free (402) rende il run `partial`, non
        lo blocca."""
        result = FmpPipeline(rollback_db, symbols=["AAPL"]).run()
        if result.failures and all("429" in f.error for f in result.failures) and len(result.failures) == 5:
            pytest.skip("quota giornaliera FMP esaurita (429 su tutti gli endpoint): condizione dell'ambiente")

        with rollback_db.transaction() as tx:
            events = tx.repository(CompanyEvent).select(
                where=[CompanyEvent.asset_id == _asset_id(rollback_db, "AAPL"), CompanyEvent.source == "FMP",
                       CompanyEvent.fetched_at >= _NOW - timedelta(minutes=1)],
            )
        assert result.status in ("success", "partial")
        assert result.targets_total == 5
        assert events, "almeno un endpoint deve aver scritto qualcosa per AAPL"
        assert {e.event_type for e in events} <= {"income_statement", "balance_sheet", "cash_flow", "dividend", "split"}
        statements = [e for e in events if e.event_type == "income_statement"]
        if statements:
            assert statements[0].fiscal_year and statements[0].reported_currency
