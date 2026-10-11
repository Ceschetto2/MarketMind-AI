"""Test unitari delle funzioni pure di `marketmind_frontend.app` (tabelle,
grafici, formattazione) — nessun DB, nessun runtime Streamlit: le funzioni di
pagina restano non testate qui, stesso limite degli entry point delle
pipeline."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd

from marketmind_frontend.analytics import PositionValuation
from marketmind_frontend.app import (
    _age,
    _backtests_dataframe,
    _comparison_chart,
    _decisions_dataframe,
    _equity_chart,
    _ingestion_dataframe,
    _pct,
    _runs_dataframe,
    _status_label,
    _truncate,
    _valuation_dataframe,
)
from marketmind_frontend.views import BacktestView, DecisionView, IngestionRunView, PortfolioView, RunView

T0 = datetime(2026, 10, 1, 10, tzinfo=timezone.utc)


class TestFormatting:
    def test_pct(self):
        assert _pct(0.0123) == "+1.23%"
        assert _pct(-0.5) == "-50.00%"
        assert _pct(None) == "—"
        assert _pct(float("nan")) == "—"

    def test_age(self):
        assert _age(timedelta(minutes=14)) == "14 min"
        assert _age(timedelta(hours=5)) == "5 h"
        assert _age(timedelta(days=3)) == "3 g"

    def test_truncate(self):
        assert _truncate("breve", 10) == "breve"
        assert _truncate("x" * 20, 10) == "x" * 9 + "…"

    def test_status_label(self):
        assert _status_label("failed") == "❌ failed"
        assert _status_label(None) == "n/d"


class TestPortfolioLabel:
    def test_suspended_portfolio_is_marked(self):
        p = PortfolioView(1, "gemma-growth-test", "model", False, 1.0, 1.0, T0)

        assert p.label == "gemma-growth-test (model, sospeso)"

    def test_active_portfolio(self):
        p = PortfolioView(1, "spy-benchmark", "benchmark", True, 1.0, 1.0, T0)

        assert p.label == "spy-benchmark (benchmark)"


class TestTables:
    def test_valuation_table(self):
        row = PositionValuation("AAPL", 2.0, 10.0, 20.0, 12.0, T0, 24.0, 4.0, 0.2)

        df = _valuation_dataframe([row])

        assert list(df["Symbol"]) == ["AAPL"]
        assert list(df["P&L"]) == [4.0]

    def test_decisions_table_in_local_time(self):
        d = DecisionView(7, T0, "AAPL", "BUY", 0.8, 0.2, "trend")

        df = _decisions_dataframe([d])

        assert list(df["Decisione"]) == ["BUY"]
        assert df["Data"].iloc[0].hour == 12  # Europe/Rome, ora legale

    def test_decisions_table_shows_requested_recheck(self):
        d = DecisionView(7, T0, "AAPL", "HOLD", None, None, "attendo", T0 + timedelta(hours=4), "trimestrale")

        df = _decisions_dataframe([d])

        assert df["Ricontrollo"].iloc[0].hour == 16  # 14 UTC -> 16 Europe/Rome
        assert list(df["Motivo rinvio"]) == ["trimestrale"]

    def test_runs_table_shows_outcome_and_error(self):
        runs = [RunView(42, T0, "gemini-x", "failed", T0, "429 quota", 3), RunView(41, T0, "gemini-x", None, None, None, 0)]

        df = _runs_dataframe(runs)

        assert list(df["Esito"]) == ["❌ failed", "n/d"]
        assert df["Errore"].iloc[0] == "429 quota"
        assert pd.isna(df["Errore"].iloc[1])

    def test_backtests_table(self):
        b = BacktestView(1, None, 75.0, 1.2, -0.01, 0.5, date(2026, 9, 14), date(2026, 10, 11), 0.0075, 0.02, T0)

        df = _backtests_dataframe([b])

        assert list(df["Periodo"]) == ["2026-09-14 → 2026-10-11"]
        assert list(df["Rendimento"]) == [0.0075]
        assert list(df["SPY stesso periodo"]) == [0.02]

    def test_ingestion_table_with_age_and_duration(self):
        run = IngestionRunView("fred", "market_data.t_macro_events", T0, T0 + timedelta(minutes=2), "success", 10, None)

        df = _ingestion_dataframe([run], now=T0 + timedelta(hours=3))

        assert list(df["Età"]) == ["3 h"]
        assert list(df["Durata"]) == ["2 min"]

    def test_empty_tables(self):
        assert _decisions_dataframe([]).empty
        assert _runs_dataframe([]).empty


class TestCharts:
    def test_equity_chart_has_equity_and_cash(self):
        curve = pd.DataFrame({"cash": [1.0, 0.5], "positions": [0.0, 0.6], "equity": [1.0, 1.1]},
                             index=[T0, T0 + timedelta(hours=1)])

        fig = _equity_chart(curve)

        assert [t.name for t in fig.data] == ["Equity", "di cui cash"]

    def test_comparison_chart_without_benchmark_prices_has_one_series(self):
        comparison = pd.DataFrame({"portfolio": [0.0, 0.1], "benchmark": [float("nan")] * 2},
                                  index=[T0, T0 + timedelta(hours=1)])

        assert [t.name for t in _comparison_chart(comparison, "SPY").data] == ["Portfolio"]

    def test_comparison_chart_with_benchmark(self):
        comparison = pd.DataFrame({"portfolio": [0.0, 0.1], "benchmark": [0.0, 0.05]},
                                  index=[T0, T0 + timedelta(hours=1)])

        assert [t.name for t in _comparison_chart(comparison, "SPY").data] == ["Portfolio", "SPY buy & hold"]
