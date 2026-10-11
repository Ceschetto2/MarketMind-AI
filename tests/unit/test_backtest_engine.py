"""Test unitari del Backtesting Engine (`backtest/engine.py`): ricostruzione
degli ordini dallo storico, simulazione vectorbt e metriche sul periodo
(funzioni pure), e `Backtester` con uno store in memoria. Nessun DB.

Il backtest lavora sull'intero portfolio: gli ordini si ricostruiscono da
tutti gli snapshot di posizione, con il prezzo di esecuzione ricavato dalla
variazione di cash dello stesso trade (|Δcash| / |Δquantità|), esatto sia
per i BUY sia per i SELL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import pytest

from marketmind_llm_decision_engine.backtest.engine import (
    Backtester,
    BacktestPeriod,
    InconsistentHistoryError,
    NoTradesError,
    Order,
    _none_if_nan,
    compute_metrics,
    reconstruct_orders,
)
from marketmind_llm_decision_engine.repositories.backtest import CashPoint, PortfolioInfo, PositionEvent


def _ts(day, hour=0):
    return datetime(2026, 9, day, hour, tzinfo=timezone.utc)


INCEPTION = _ts(13)
AS_OF = _ts(20)


def _event(ts, quantity, avg_price, operation, asset_id=1, symbol="AAPL"):
    return PositionEvent(asset_id, symbol, ts, quantity, avg_price, operation)


def _cash(*points):
    return [CashPoint(ts, cash) for ts, cash in points]


class TestReconstructOrders:
    def test_buy_poi_rafforzamento_poi_vendita_parziale_poi_chiusura(self):
        events = [
            _event(_ts(14), 10.0, 100.0, "INSERT"),
            _event(_ts(15), 15.0, 104.0, "UPDATE"),   # +5 a 112: (15*104 - 10*100) / 5
            _event(_ts(16), 5.0, 104.0, "UPDATE"),    # -10, avg invariato
            _event(_ts(17), 5.0, 104.0, "DELETE"),    # chiusura delle 5 rimaste
        ]
        cash = _cash((INCEPTION, 10_000.0), (_ts(14), 9_000.0), (_ts(15), 8_440.0),
                     (_ts(16), 9_640.0), (_ts(17), 10_240.0))

        orders = reconstruct_orders(events, cash)

        assert [(o.ts, o.size, o.price) for o in orders] == [
            (_ts(14), 10.0, pytest.approx(100.0)),
            (_ts(15), 5.0, pytest.approx(112.0)),
            (_ts(16), -10.0, pytest.approx(120.0)),
            (_ts(17), -5.0, pytest.approx(120.0)),
        ]

    def test_snapshot_di_cash_senza_variazione_ignorati(self):
        """Ogni UPDATE di t_portfolios scrive uno snapshot, anche quando cambia
        solo next_decision_at: non è un trade."""
        events = [_event(_ts(14), 10.0, 100.0, "INSERT")]
        cash = _cash((INCEPTION, 10_000.0), (_ts(13, 12), 10_000.0), (_ts(14), 9_000.0), (_ts(15), 9_000.0))

        [order] = reconstruct_orders(events, cash)

        assert (order.size, order.price) == (10.0, pytest.approx(100.0))

    def test_piu_asset_in_ordine(self):
        events = [_event(_ts(14), 10.0, 100.0, "INSERT"),
                  _event(_ts(15), 4.0, 50.0, "INSERT", asset_id=2, symbol="MSFT")]
        cash = _cash((INCEPTION, 10_000.0), (_ts(14), 9_000.0), (_ts(15), 8_800.0))

        orders = reconstruct_orders(events, cash)

        assert [(o.symbol, o.size, o.price) for o in orders] == [("AAPL", 10.0, 100.0), ("MSFT", 4.0, 50.0)]

    def test_storico_incoerente_si_ferma(self):
        events = [_event(_ts(14), 10.0, 100.0, "INSERT"), _event(_ts(15), 15.0, 104.0, "UPDATE")]
        cash = _cash((INCEPTION, 10_000.0), (_ts(14), 9_000.0))

        with pytest.raises(InconsistentHistoryError, match="2 eventi di posizione, 1 variazioni di cash"):
            reconstruct_orders(events, cash)

    def test_nessun_evento(self):
        assert reconstruct_orders([], _cash((INCEPTION, 1.0))) == []


def _orders_buy_then_sell():
    """10 AAPL comprate a 100 il 14, vendute a 125 il 17."""
    return [Order(_ts(14), 1, "AAPL", 10.0, 100.0), Order(_ts(17), 1, "AAPL", -10.0, 125.0)]


CLOSES = {"AAPL": [(_ts(14), 100.0), (_ts(15), 110.0), (_ts(16), 90.0), (_ts(17), 120.0), (_ts(18), 130.0)]}
SPY = [(_ts(13), 500.0), (_ts(18), 510.0)]


class TestComputeMetrics:
    def test_intero_storico(self):
        metrics = compute_metrics(
            _orders_buy_then_sell(), CLOSES, starting_capital=1_000.0, inception=INCEPTION,
            period=BacktestPeriod(_ts(13), _ts(18)), benchmark_closes=SPY,
        )

        assert metrics.pnl == pytest.approx(250.0)
        assert metrics.total_return == pytest.approx(0.25)
        assert metrics.final_cash == pytest.approx(1_250.0)
        # picco 1100 il 15, minimo 900 il 16
        assert metrics.max_drawdown == pytest.approx(900 / 1_100 - 1)
        assert metrics.win_rate == 1.0
        assert metrics.trades == 2
        assert metrics.benchmark_return == pytest.approx(510 / 500 - 1)
        assert (metrics.period_start, metrics.period_end) == (date(2026, 9, 13), date(2026, 9, 18))

    def test_periodo_parziale_parte_dal_valore_a_quella_data(self):
        """La simulazione parte sempre dalla nascita del portfolio (vectorbt non
        sa partire con posizioni aperte); il periodo taglia solo le metriche."""
        metrics = compute_metrics(
            _orders_buy_then_sell(), CLOSES, starting_capital=1_000.0, inception=INCEPTION,
            period=BacktestPeriod(_ts(16), _ts(18)), benchmark_closes=SPY,
        )

        # valore il 16: 10 * 90 = 900; a fine periodo 1250 in cash
        assert metrics.pnl == pytest.approx(350.0)
        assert metrics.total_return == pytest.approx(1_250 / 900 - 1)
        assert metrics.trades == 1

    def test_trade_aperto_non_conta_nel_win_rate(self):
        metrics = compute_metrics(
            [Order(_ts(14), 1, "AAPL", 10.0, 100.0)], CLOSES, starting_capital=1_000.0, inception=INCEPTION,
            period=BacktestPeriod(_ts(13), _ts(18)), benchmark_closes=[],
        )

        assert metrics.pnl == pytest.approx(300.0)
        assert metrics.win_rate is None
        assert metrics.benchmark_return is None

    def test_sharpe_sul_valore_giornaliero(self):
        metrics = compute_metrics(
            _orders_buy_then_sell(), CLOSES, starting_capital=1_000.0, inception=INCEPTION,
            period=BacktestPeriod(_ts(13), _ts(18)), benchmark_closes=SPY,
        )

        assert metrics.sharpe_ratio is not None

    def test_ordini_nello_stesso_istante_non_si_perdono(self):
        orders = [Order(_ts(14), 1, "AAPL", 10.0, 100.0), Order(_ts(14), 1, "AAPL", -5.0, 110.0)]

        metrics = compute_metrics(
            orders, CLOSES, starting_capital=1_000.0, inception=INCEPTION,
            period=BacktestPeriod(_ts(13), _ts(18)), benchmark_closes=[],
        )

        # 5 vendute a 110 (+50), 5 rimaste a 130 (+150)
        assert metrics.final_cash == pytest.approx(550.0)
        assert metrics.pnl == pytest.approx(200.0)

    def test_nessun_ordine(self):
        with pytest.raises(NoTradesError):
            compute_metrics([], CLOSES, starting_capital=1.0, inception=INCEPTION,
                            period=BacktestPeriod(_ts(13), _ts(18)), benchmark_closes=[])


@dataclass
class FakeBacktestStore:
    events: list[PositionEvent]
    cash: list[CashPoint]
    prices: dict[int, list[tuple[datetime, float]]]
    current_cash: float = 1_250.0
    starting_capital: float = 1_000.0
    benchmark: list[tuple[datetime, float]] = field(default_factory=lambda: list(SPY))
    written: list[tuple[int, object]] = field(default_factory=list)

    def portfolio_info(self, portfolio_id):
        return PortfolioInfo(portfolio_id, "p", self.starting_capital, self.current_cash, INCEPTION)

    def position_events(self, portfolio_id):
        return self.events

    def cash_history(self, portfolio_id):
        return self.cash

    def price_history(self, asset_id, *, as_of):
        return self.prices[asset_id]

    def benchmark_history(self, *, as_of):
        return self.benchmark

    def write_result(self, portfolio_id, metrics):
        self.written.append((portfolio_id, metrics))
        return 7


def _store(**overrides):
    values = dict(
        events=[_event(_ts(14), 10.0, 100.0, "INSERT"), _event(_ts(17), 10.0, 100.0, "DELETE")],
        cash=_cash((INCEPTION, 1_000.0), (_ts(14), 0.0), (_ts(17), 1_250.0)),
        prices={1: CLOSES["AAPL"]},
    )
    values.update(overrides)
    return FakeBacktestStore(**values)


class TestBacktester:
    def test_calcola_e_salva_sullintero_storico(self):
        store = _store()

        metrics, backtest_id = Backtester(store).run(portfolio_id=5, as_of=AS_OF)

        assert metrics.pnl == pytest.approx(250.0)
        assert metrics.period_start == INCEPTION.date() and metrics.period_end == AS_OF.date()
        assert backtest_id == 7 and store.written == [(5, metrics)]

    def test_periodo_esplicito(self):
        metrics, _ = Backtester(_store()).run(
            portfolio_id=5, as_of=AS_OF, start=date(2026, 9, 16), end=date(2026, 9, 18), save=False
        )

        assert metrics.period_start == date(2026, 9, 16)
        assert metrics.pnl == pytest.approx(350.0)

    def test_senza_salvataggio(self):
        store = _store()

        _, backtest_id = Backtester(store).run(portfolio_id=5, as_of=AS_OF, save=False)

        assert backtest_id is None and store.written == []

    def test_cash_simulato_diverso_da_quello_reale_segnalato(self, caplog):
        store = _store(current_cash=999.0)

        with caplog.at_level("WARNING"):
            Backtester(store).run(portfolio_id=5, as_of=AS_OF, save=False)

        assert "cash simulato" in caplog.text

    def test_portfolio_senza_trade_non_scrive(self):
        store = _store(events=[], cash=_cash((INCEPTION, 1_000.0)))

        with pytest.raises(NoTradesError):
            Backtester(store).run(portfolio_id=5, as_of=AS_OF)
        assert store.written == []

    def test_periodo_invertito(self):
        with pytest.raises(ValueError, match="periodo"):
            Backtester(_store()).run(portfolio_id=5, as_of=AS_OF, start=date(2026, 9, 18), end=date(2026, 9, 16))


class TestNoneIfNan:
    def test_nan_diventa_none(self):
        assert _none_if_nan(float("nan")) is None

    def test_valore_normale(self):
        assert _none_if_nan(1.5) == 1.5
