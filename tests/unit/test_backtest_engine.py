"""Test unitari del Backtesting Engine (`backtest/engine.py`): la parte
vectorbt (`compute_metrics`, pura) e `Backtester` con uno store in memoria.
Nessun DB."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from marketmind_llm_decision_engine.backtest.engine import (
    Backtester,
    NoTradesForRunError,
    _none_if_nan,
    compute_metrics,
)
from marketmind_llm_decision_engine.repositories.backtest import PositionEvent

AS_OF = datetime(2026, 9, 20, tzinfo=timezone.utc)


def _event(asset_id, symbol, ts, quantity, avg_price, operation="INSERT"):
    return PositionEvent(asset_id, symbol, ts, quantity, avg_price, operation)


def _ts(day, hour=0):
    return datetime(2026, 9, day, hour, tzinfo=timezone.utc)


class TestComputeMetrics:
    def test_pnl_positivo_da_apprezzamento(self):
        entry = _ts(14, 12)
        metrics = compute_metrics(
            [_event(1, "AAPL", entry, 10.0, 100.0)],
            {"AAPL": [(_ts(15), 110.0), (_ts(16), 120.0)]},
            starting_capital=10_000.0,
        )

        # 10 azioni comprate a 100, ultimo prezzo noto 120 -> +20/azione
        assert metrics.pnl == pytest.approx(200.0)
        assert metrics.period_start == entry.date()
        assert metrics.period_end == _ts(16).date()

    def test_cash_sharing_tra_piu_asset(self):
        metrics = compute_metrics(
            [_event(1, "AAPL", _ts(14, 12), 10.0, 100.0), _event(2, "MSFT", _ts(14, 13), 5.0, 50.0)],
            {"AAPL": [(_ts(15), 110.0)], "MSFT": [(_ts(15), 40.0)]},
            starting_capital=10_000.0,
        )

        # AAPL: 10 * (110-100) = +100; MSFT: 5 * (40-50) = -50 -> netto +50
        assert metrics.pnl == pytest.approx(50.0)

    def test_inizio_periodo_e_il_primo_trade_non_il_primo_prezzo(self):
        entry = _ts(14, 12)
        metrics = compute_metrics(
            [_event(1, "AAPL", entry, 1.0, 100.0)],
            {"AAPL": [(datetime(2026, 1, 1, tzinfo=timezone.utc), 90.0)]},
            starting_capital=10_000.0,
        )

        assert metrics.period_start == entry.date()

    def test_nessun_trade(self):
        with pytest.raises(NoTradesForRunError):
            compute_metrics([], {}, starting_capital=1.0)

    def test_operazioni_diverse_da_insert_non_supportate(self):
        with pytest.raises(NotImplementedError, match="UPDATE"):
            compute_metrics([_event(1, "AAPL", _ts(14), 10.0, 100.0, operation="UPDATE")], {"AAPL": []}, starting_capital=1.0)


@dataclass
class FakeBacktestStore:
    events: list[PositionEvent]
    prices: dict[int, list[tuple[datetime, float]]]
    starting_capital: float = 10_000.0
    written: list[tuple[int, object]] = field(default_factory=list)

    def position_events(self, portfolio_id, run_id):
        return self.events

    def price_history(self, asset_id, *, as_of):
        return self.prices[asset_id]

    def starting_capital_of(self, portfolio_id):
        return self.starting_capital

    def write_result(self, run_id, metrics):
        self.written.append((run_id, metrics))
        return 7


class TestBacktester:
    def test_calcola_e_salva(self):
        store = FakeBacktestStore([_event(1, "AAPL", _ts(14, 12), 10.0, 100.0)], {1: [(_ts(16), 120.0)]})

        metrics, backtest_id = Backtester(store).run(portfolio_id=1, run_id=41, as_of=AS_OF)

        assert metrics.pnl == pytest.approx(200.0)
        assert backtest_id == 7 and store.written == [(41, metrics)]

    def test_senza_salvataggio(self):
        store = FakeBacktestStore([_event(1, "AAPL", _ts(14, 12), 10.0, 100.0)], {1: [(_ts(16), 120.0)]})

        _, backtest_id = Backtester(store).run(portfolio_id=1, run_id=41, as_of=AS_OF, save=False)

        assert backtest_id is None and store.written == []

    def test_run_senza_trade_non_scrive(self):
        store = FakeBacktestStore([], {})

        with pytest.raises(NoTradesForRunError):
            Backtester(store).run(portfolio_id=1, run_id=41, as_of=AS_OF)
        assert store.written == []


class TestNoneIfNan:
    def test_nan_diventa_none(self):
        assert _none_if_nan(float("nan")) is None

    def test_valore_normale(self):
        assert _none_if_nan(1.5) == 1.5
