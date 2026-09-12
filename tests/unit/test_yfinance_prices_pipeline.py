"""Test unitari per la logica pura di `yfinance_prices_pipeline.py`.

Nessun accesso a rete/DB reale: `yf.Ticker`, `get_session` e lo strato di
scrittura (`db/writer.py`) sono mockati con `pytest-mock`. `get_universe_symbols()`
non è coperta qui (tocca il DB) — è compito del test di integrazione.
"""

from __future__ import annotations

import pandas as pd
import pytest

from marketmind_ai.db.writer import AssetNotFoundError
from marketmind_ai.ingestion.yfinance_prices_pipeline import (
    _fetch_history,
    _rows_to_records,
    run,
)
from marketmind_ai.schemas import MarketPriceRecord


def _make_history_df() -> pd.DataFrame:
    """DataFrame finto nella stessa forma di `yf.Ticker(...).history()`:
    indice = timestamp, colonne OHLCV con nomi/case di yfinance."""
    index = pd.date_range("2026-09-06 09:00", periods=2, freq="60min", tz="UTC")
    return pd.DataFrame(
        {
            "Open": [100.0, 101.5],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.5],
            "Close": [101.0, 102.5],
            "Volume": [1_000_000, 950_000],
        },
        index=index,
    )


class TestRowsToRecords:
    def test_maps_columns_and_converts_types(self):
        history = _make_history_df()

        records = _rows_to_records("AAPL", history)

        assert len(records) == 2
        assert all(isinstance(r, MarketPriceRecord) for r in records)

        first = records[0]
        assert first.symbol == "AAPL"
        assert first.source == "yfinance"
        assert first.ts == history.index[0].to_pydatetime()
        assert first.open == 100.0
        assert first.high == 102.0
        assert first.low == 99.0
        assert first.close == 101.0
        assert first.volume == 1_000_000
        # numpy.float64/int64 del DataFrame vanno convertiti ai tipi python
        # nativi attesi da Pydantic, non lasciati come tipi numpy.
        assert type(first.open) is float
        assert type(first.volume) is int

        second = records[1]
        assert second.close == 102.5
        assert second.volume == 950_000

    def test_empty_history_returns_empty_list(self):
        empty = _make_history_df().iloc[0:0]

        records = _rows_to_records("AAPL", empty)

        assert records == []


class TestFetchHistoryRetry:
    """`_fetch_history` è decorata con `tenacity.retry` (stop_after_attempt(4),
    wait_exponential reale fino a 30s). Per non far durare il test azzeriamo
    `sleep` sull'oggetto `Retrying` attaccato alla funzione decorata
    (`_fetch_history.retry.sleep`) invece di toccare la configurazione del
    decoratore in produzione — il conteggio dei tentativi resta quello vero.
    """

    def test_retries_and_eventually_succeeds(self, mocker):
        expected_df = _make_history_df()
        mock_ticker = mocker.Mock()
        mock_ticker.history.side_effect = [
            ConnectionError("rete non raggiungibile"),
            ConnectionError("rete non raggiungibile"),
            expected_df,
        ]
        mocker.patch(
            "marketmind_ai.ingestion.yfinance_prices_pipeline.yf.Ticker",
            return_value=mock_ticker,
        )
        mocker.patch.object(_fetch_history.retry, "sleep", lambda _seconds: None)

        result = _fetch_history("AAPL")

        assert result is expected_df
        assert mock_ticker.history.call_count == 3

    def test_propagates_after_stop_after_attempt(self, mocker):
        mock_ticker = mocker.Mock()
        mock_ticker.history.side_effect = ConnectionError("rete non raggiungibile")
        mocker.patch(
            "marketmind_ai.ingestion.yfinance_prices_pipeline.yf.Ticker",
            return_value=mock_ticker,
        )
        mocker.patch.object(_fetch_history.retry, "sleep", lambda _seconds: None)

        with pytest.raises(ConnectionError):
            _fetch_history("AAPL")

        # stop_after_attempt(4): 1 tentativo iniziale + 3 retry, poi propaga.
        assert mock_ticker.history.call_count == 4


class TestRun:
    """Flusso di alto livello di `run()`, con tutte le dipendenze esterne
    (universo, fetch, sessione DB, strato di scrittura, audit) mockate."""

    def test_asset_not_found_for_one_symbol_does_not_block_the_others(self, mocker):
        mocker.patch(
            "marketmind_ai.ingestion.yfinance_prices_pipeline.get_universe_symbols",
            return_value=["AAA", "BBB"],
        )
        # niente attese reali tra i ticker
        mocker.patch("marketmind_ai.ingestion.yfinance_prices_pipeline.time.sleep")

        history_aaa = mocker.Mock(empty=False)
        history_bbb = mocker.Mock(empty=False)
        mocker.patch(
            "marketmind_ai.ingestion.yfinance_prices_pipeline._fetch_history",
            side_effect=[history_aaa, history_bbb],
        )

        records_aaa = [mocker.Mock(name="record_aaa")]
        records_bbb = [mocker.Mock(name="record_bbb")]
        mocker.patch(
            "marketmind_ai.ingestion.yfinance_prices_pipeline._rows_to_records",
            side_effect=[records_aaa, records_bbb],
        )

        mock_session = mocker.MagicMock(name="session")
        mock_get_session = mocker.patch(
            "marketmind_ai.ingestion.yfinance_prices_pipeline.get_session"
        )
        mock_get_session.return_value.__enter__.return_value = mock_session

        # AAA non ancora in t_assets: la pipeline logga e passa a BBB senza
        # interrompersi (comportamento sotto test).
        mock_resolve_asset_id = mocker.patch(
            "marketmind_ai.ingestion.yfinance_prices_pipeline.resolve_asset_id",
            side_effect=[AssetNotFoundError("AAA non trovato in t_assets"), 42],
        )
        mock_upsert = mocker.patch(
            "marketmind_ai.ingestion.yfinance_prices_pipeline.upsert_market_price"
        )

        tracker = mocker.Mock(rows_written=0)
        mock_ingestion_run = mocker.patch(
            "marketmind_ai.ingestion.yfinance_prices_pipeline.ingestion_run"
        )
        mock_ingestion_run.return_value.__enter__.return_value = tracker

        run()

        assert mock_resolve_asset_id.call_count == 2
        # solo BBB arriva all'upsert: AAA è stato saltato dopo l'eccezione.
        mock_upsert.assert_called_once_with(mock_session, 42, records_bbb[0])
        assert tracker.rows_written == len(records_bbb)
