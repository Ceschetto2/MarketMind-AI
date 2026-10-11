"""Test unitari dei calcoli della dashboard (`marketmind_frontend.analytics`):
equity curve mark-to-market, valorizzazione delle posizioni, confronto col
benchmark. Funzioni pure su valori, nessun DB."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from marketmind_frontend.analytics import (
    benchmark_comparison,
    equity_curve,
    performance_summary,
    valuation,
)
from marketmind_frontend.views import CashPoint, PositionChange, PositionView, PricePoint

T0 = datetime(2026, 10, 1, 10, tzinfo=timezone.utc)


def _h(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


class TestEquityCurve:
    def test_only_cash_when_no_positions(self):
        curve = equity_curve([CashPoint(T0, 1_000.0)], [], [])

        assert list(curve["equity"]) == [1_000.0]
        assert list(curve["positions"]) == [0.0]

    def test_position_valued_at_latest_close(self):
        cash = [CashPoint(T0, 1_000.0), CashPoint(_h(1), 500.0)]
        changes = [PositionChange(1, "AAPL", _h(1), 5.0, 100.0, "INSERT")]
        prices = [PricePoint(1, _h(1), 100.0), PricePoint(1, _h(2), 120.0)]

        curve = equity_curve(cash, changes, prices)

        assert curve.loc[_h(1), "equity"] == pytest.approx(1_000.0)
        assert curve.loc[_h(2), "positions"] == pytest.approx(600.0)
        assert curve.loc[_h(2), "equity"] == pytest.approx(1_100.0)

    def test_uses_last_close_before_the_trade(self):
        """Una barra precedente all'inizio della curva serve comunque a
        valorizzare la posizione: il prezzo vale fino alla barra dopo."""
        cash = [CashPoint(T0, 1_000.0), CashPoint(_h(1), 500.0)]
        changes = [PositionChange(1, "AAPL", _h(1), 5.0, 100.0, "INSERT")]
        prices = [PricePoint(1, T0 - timedelta(days=1), 110.0)]

        curve = equity_curve(cash, changes, prices)

        assert list(curve.index) == [T0, _h(1)]
        assert curve.loc[_h(1), "equity"] == pytest.approx(500.0 + 5 * 110.0)

    def test_falls_back_to_avg_price_without_any_close(self):
        cash = [CashPoint(T0, 1_000.0), CashPoint(_h(1), 500.0)]
        changes = [PositionChange(1, "AAPL", _h(1), 5.0, 100.0, "INSERT")]

        curve = equity_curve(cash, changes, [])

        assert curve.loc[_h(1), "equity"] == pytest.approx(1_000.0)

    def test_delete_closes_the_position(self):
        cash = [CashPoint(T0, 1_000.0), CashPoint(_h(1), 500.0), CashPoint(_h(3), 1_200.0)]
        changes = [
            PositionChange(1, "AAPL", _h(1), 5.0, 100.0, "INSERT"),
            PositionChange(1, "AAPL", _h(3), 5.0, 100.0, "DELETE"),
        ]
        prices = [PricePoint(1, _h(1), 100.0), PricePoint(1, _h(2), 140.0), PricePoint(1, _h(4), 90.0)]

        curve = equity_curve(cash, changes, prices)

        assert curve.loc[_h(2), "equity"] == pytest.approx(500.0 + 700.0)
        assert curve.loc[_h(3), "positions"] == 0.0
        assert curve.loc[_h(4), "equity"] == pytest.approx(1_200.0)

    def test_multiple_assets_are_summed(self):
        cash = [CashPoint(T0, 1_000.0), CashPoint(_h(1), 0.0)]
        changes = [
            PositionChange(1, "AAPL", _h(1), 5.0, 100.0, "INSERT"),
            PositionChange(2, "MSFT", _h(1), 10.0, 50.0, "INSERT"),
        ]
        prices = [PricePoint(1, _h(2), 110.0), PricePoint(2, _h(2), 40.0)]

        curve = equity_curve(cash, changes, prices)

        assert curve.loc[_h(2), "equity"] == pytest.approx(550.0 + 400.0)

    def test_empty_without_cash_history(self):
        assert equity_curve([], [], []).empty


class TestValuation:
    def test_market_value_and_unrealized_pnl(self):
        positions = [PositionView(1, "AAPL", 10.0, 100.0)]
        latest = {1: PricePoint(1, T0, 120.0)}

        [row] = valuation(positions, latest)

        assert row.cost == 1_000.0
        assert row.market_value == 1_200.0
        assert row.pnl == 200.0
        assert row.pnl_pct == pytest.approx(0.2)
        assert row.price_ts == T0

    def test_without_a_price_valued_at_cost(self):
        [row] = valuation([PositionView(1, "AAPL", 10.0, 100.0)], {})

        assert row.last_price is None
        assert row.market_value == 1_000.0
        assert row.pnl == 0.0


class TestPerformanceSummary:
    def test_total_return_and_max_drawdown(self):
        cash = [CashPoint(T0, 1_000.0), CashPoint(_h(1), 0.0)]
        changes = [PositionChange(1, "AAPL", _h(1), 10.0, 100.0, "INSERT")]
        prices = [PricePoint(1, _h(2), 120.0), PricePoint(1, _h(3), 90.0), PricePoint(1, _h(4), 110.0)]

        summary = performance_summary(equity_curve(cash, changes, prices), starting_capital=1_000.0)

        assert summary.equity == pytest.approx(1_100.0)
        assert summary.total_return == pytest.approx(0.10)
        assert summary.max_drawdown == pytest.approx(900 / 1_200 - 1)

    def test_none_without_curve(self):
        summary = performance_summary(equity_curve([], [], []), starting_capital=1_000.0)

        assert summary.equity is None
        assert summary.total_return is None
        assert summary.max_drawdown is None


class TestBenchmarkComparison:
    def test_both_series_rebased_to_the_start_of_the_curve(self):
        curve = equity_curve([CashPoint(T0, 1_000.0), CashPoint(_h(2), 1_100.0)], [], [])
        spy = [PricePoint(9, T0 - timedelta(hours=1), 500.0), PricePoint(9, _h(1), 550.0)]

        comparison = benchmark_comparison(curve, 1_000.0, spy)

        assert comparison.loc[T0, "benchmark"] == pytest.approx(0.0)
        assert comparison.loc[_h(1), "benchmark"] == pytest.approx(0.10)
        assert comparison.loc[_h(1), "portfolio"] == pytest.approx(0.0)
        assert comparison.loc[_h(2), "portfolio"] == pytest.approx(0.10)

    def test_benchmark_starts_at_its_first_bar_if_none_before(self):
        curve = equity_curve([CashPoint(T0, 1_000.0)], [], [])
        spy = [PricePoint(9, _h(1), 500.0), PricePoint(9, _h(2), 450.0)]

        comparison = benchmark_comparison(curve, 1_000.0, spy)

        assert comparison.loc[_h(1), "benchmark"] == pytest.approx(0.0)
        assert comparison.loc[_h(2), "benchmark"] == pytest.approx(-0.10)

    def test_no_benchmark_column_without_prices(self):
        curve = equity_curve([CashPoint(T0, 1_000.0)], [], [])

        comparison = benchmark_comparison(curve, 1_000.0, [])

        assert comparison["benchmark"].isna().all()
