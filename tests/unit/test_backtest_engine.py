"""Test unitari per `backtest/engine.py`. Nessun accesso a DB/rete: le
letture (`get_position_events`/`get_recent_prices`/`get_portfolio`) sono
mockate; vectorbt gira per davvero su dati sintetici (calcolo puro, nessun
I/O esterno — stesso principio per cui i test di `llm/gemini.py` validano
Pydantic per davvero, solo la chiamata di rete è mockata).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from marketmind_ai.backtest.engine import (
    NoTradesForRunError,
    _none_if_nan,
    run_backtest,
)

AS_OF = datetime(2026, 9, 20, tzinfo=timezone.utc)


def _price(mocker, ts, close):
    return mocker.Mock(ts=ts, close=close)


def _event(mocker, asset_id, symbol, ts, quantity, avg_price, operation="INSERT"):
    asset = mocker.Mock(symbol=symbol)
    return mocker.Mock(
        asset_id=asset_id,
        asset=asset,
        changed_at=ts,
        quantity=quantity,
        avg_price=avg_price,
        operation=operation,
    )


def _patch(mocker, events, prices_by_asset, starting_capital=10_000.0):
    mocker.patch(
        "marketmind_ai.backtest.engine.get_position_events", return_value=events
    )
    mocker.patch(
        "marketmind_ai.backtest.engine.get_recent_prices",
        side_effect=lambda session, asset_id, as_of, days_back: prices_by_asset[asset_id],
    )
    mocker.patch(
        "marketmind_ai.backtest.engine.get_portfolio",
        return_value=mocker.Mock(starting_capital=starting_capital),
    )


class TestRunBacktest:
    def test_raises_when_no_trades_for_run(self, mocker):
        _patch(mocker, events=[], prices_by_asset={})

        with pytest.raises(NoTradesForRunError):
            run_backtest(mocker.Mock(), portfolio_id=1, run_id=41, as_of=AS_OF)

    def test_raises_on_unsupported_operation(self, mocker):
        events = [
            _event(mocker, 1, "AAPL", datetime(2026, 9, 14, tzinfo=timezone.utc), 10.0, 100.0, operation="UPDATE")
        ]
        _patch(mocker, events, prices_by_asset={1: []})

        with pytest.raises(NotImplementedError):
            run_backtest(mocker.Mock(), portfolio_id=1, run_id=41, as_of=AS_OF)

    def test_computes_positive_pnl_from_price_appreciation(self, mocker):
        entry_ts = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
        events = [_event(mocker, 1, "AAPL", entry_ts, quantity=10.0, avg_price=100.0)]
        prices = {
            1: [
                _price(mocker, datetime(2026, 9, 15, tzinfo=timezone.utc), 110.0),
                _price(mocker, datetime(2026, 9, 16, tzinfo=timezone.utc), 120.0),
            ]
        }
        _patch(mocker, events, prices, starting_capital=10_000.0)

        metrics = run_backtest(mocker.Mock(), portfolio_id=1, run_id=41, as_of=AS_OF)

        # 10 azioni comprate a 100, ultimo prezzo noto 120 -> +20/azione
        assert metrics.pnl == pytest.approx(200.0)
        assert metrics.period_start == entry_ts.date()
        assert metrics.period_end == datetime(2026, 9, 16, tzinfo=timezone.utc).date()

    def test_cash_sharing_across_multiple_assets(self, mocker):
        ts1 = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 9, 14, 13, 0, tzinfo=timezone.utc)
        events = [
            _event(mocker, 1, "AAPL", ts1, quantity=10.0, avg_price=100.0),
            _event(mocker, 2, "MSFT", ts2, quantity=5.0, avg_price=50.0),
        ]
        prices = {
            1: [_price(mocker, datetime(2026, 9, 15, tzinfo=timezone.utc), 110.0)],
            2: [_price(mocker, datetime(2026, 9, 15, tzinfo=timezone.utc), 40.0)],
        }
        _patch(mocker, events, prices, starting_capital=10_000.0)

        metrics = run_backtest(mocker.Mock(), portfolio_id=1, run_id=41, as_of=AS_OF)

        # AAPL: 10 * (110-100) = +100; MSFT: 5 * (40-50) = -50 -> netto +50
        assert metrics.pnl == pytest.approx(50.0)

    def test_period_start_is_earliest_trade_not_earliest_price(self, mocker):
        entry_ts = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
        events = [_event(mocker, 1, "AAPL", entry_ts, quantity=1.0, avg_price=100.0)]
        prices = {1: [_price(mocker, datetime(2026, 1, 1, tzinfo=timezone.utc), 90.0)]}
        _patch(mocker, events, prices)

        metrics = run_backtest(mocker.Mock(), portfolio_id=1, run_id=41, as_of=AS_OF)

        assert metrics.period_start == entry_ts.date()


class TestNoneIfNan:
    def test_returns_none_for_nan(self):
        assert _none_if_nan(float("nan")) is None

    def test_returns_float_for_normal_value(self):
        assert _none_if_nan(1.5) == 1.5
