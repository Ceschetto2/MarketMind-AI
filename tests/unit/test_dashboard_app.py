"""Test unitari per le funzioni pure di `dashboard/app.py` — nessun
accesso a DB/rete, nessun runtime Streamlit: `_render()`/`main()` restano
non testate qui (limite noto, stesso principio per cui gli entry point
delle pipeline di ingestion non hanno test unitari propri, solo le
funzioni che chiamano).
"""

from __future__ import annotations

from datetime import datetime, timezone

from marketmind_ai.dashboard.app import (
    _cash_history_dataframe,
    _cost_basis,
    _decisions_dataframe,
    _portfolio_label,
    _positions_dataframe,
)


def _position(mocker, symbol, quantity, avg_price):
    asset = mocker.Mock(symbol=symbol)
    return mocker.Mock(asset=asset, quantity=quantity, avg_price=avg_price)


def _decision(mocker, symbol, ts, decision, confidence=None, size_pct=None, reasoning=None):
    asset = mocker.Mock(symbol=symbol)
    return mocker.Mock(
        asset=asset,
        ts=ts,
        decision=decision,
        confidence=confidence,
        size_pct=size_pct,
        reasoning=reasoning,
    )


class TestPortfolioLabel:
    def test_includes_name_and_type(self, mocker):
        portfolio = mocker.Mock(name="gemma-growth-test", portfolio_type="model")
        # mocker.Mock(name=...) è riservato dalla libreria (imposta il nome
        # del mock stesso, non un attributo) — va assegnato dopo la creazione.
        portfolio.name = "gemma-growth-test"

        assert _portfolio_label(portfolio) == "gemma-growth-test (model)"


class TestCostBasis:
    def test_empty_positions_is_zero(self):
        assert _cost_basis([]) == 0

    def test_sums_quantity_times_avg_price_across_positions(self, mocker):
        positions = [
            _position(mocker, "AAPL", 10.0, 100.0),
            _position(mocker, "MSFT", 5.0, 50.0),
        ]

        assert _cost_basis(positions) == 1_000.0 + 250.0


class TestPositionsDataframe:
    def test_one_row_per_position_with_computed_cost(self, mocker):
        positions = [_position(mocker, "AAPL", 10.0, 100.0)]

        df = _positions_dataframe(positions)

        assert list(df["Symbol"]) == ["AAPL"]
        assert list(df["Costo"]) == [1_000.0]

    def test_empty_positions_gives_empty_dataframe(self):
        assert _positions_dataframe([]).empty


class TestDecisionsDataframe:
    def test_one_row_per_decision(self, mocker):
        ts = datetime(2026, 9, 14, tzinfo=timezone.utc)
        decisions = [_decision(mocker, "AAPL", ts, "BUY", confidence=0.8, size_pct=0.2, reasoning="trend")]

        df = _decisions_dataframe(decisions)

        assert list(df["Symbol"]) == ["AAPL"]
        assert list(df["Decisione"]) == ["BUY"]
        assert list(df["Reasoning"]) == ["trend"]

    def test_empty_decisions_gives_empty_dataframe(self):
        assert _decisions_dataframe([]).empty


class TestCashHistoryDataframe:
    def test_columns_match_snapshot_fields(self, mocker):
        ts = datetime(2026, 9, 14, tzinfo=timezone.utc)
        history = [mocker.Mock(changed_at=ts, cash=1_000.0)]

        df = _cash_history_dataframe(history)

        assert list(df["data"]) == [ts]
        assert list(df["cash"]) == [1_000.0]
