"""Test di integrazione della dashboard contro Postgres reale: le letture di
`DashboardRepository` (policy `READ_ONLY`) e le scritture di gestione di
`PortfolioAdmin` (policy `APP`).

Tutto gira sulla transazione annullata di `rollback_connection`: i dati di
prova (asset `TESTDB*`, portfolio `test-dash-*`) sono preparati con
`rollback_full_db`, nulla resta nel DB a fine test. Sostituisce il vecchio
`test_dashboard_reader.py`, che scriveva l'asset `TESTX` con pulizia manuale.

Dentro una sola transazione `now()` è costante: tutti gli snapshot scritti
dai trigger in un test hanno lo stesso `changed_at`, quindi l'ordine
cronologico si verifica sull'ordine di inserimento.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from marketmind_db.database import Database
from marketmind_db.exceptions import AccessDeniedError
from marketmind_db.models.audit import IngestionRun
from marketmind_db.models.decisions import BacktestResult, ModelDecision, ModelRun
from marketmind_db.models.market_data import Asset, MarketPrice, UniverseMember
from marketmind_db.models.portfolio import (
    Portfolio,
    PortfolioPosition,
    PortfolioWatchlistEntry,
)
from marketmind_frontend.admin import NewModelPortfolio, PortfolioAdmin
from marketmind_frontend.repository import DashboardRepository

pytestmark = pytest.mark.integration

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _asset(db: Database, symbol: str, *, benchmark: bool | None = None) -> int:
    with db.transaction() as tx:
        [row] = tx.repository(Asset).upsert_returning(
            [{"symbol": symbol, "name": f"{symbol} Inc.", "sector": "Test", "asset_type": "equity",
              "source": "test", "fetched_at": NOW}],
            conflict_on=("symbol",),
            returning=("asset_id",),
        )
        if benchmark is not None:
            tx.repository(UniverseMember).upsert(
                [{"asset_id": row["asset_id"], "is_benchmark": benchmark, "source": "test", "fetched_at": NOW}],
                conflict_on=("asset_id",),
            )
    return row["asset_id"]


def _portfolio(db: Database, name: str, *, cash: float = 10_000.0, portfolio_type: str = "model",
               is_active: bool = True) -> int:
    model = portfolio_type == "model"
    with db.transaction() as tx:
        [row] = tx.repository(Portfolio).insert_returning(
            [{
                "name": name, "portfolio_type": portfolio_type, "starting_capital": cash, "cash": cash,
                "equity_value": cash, "created_at": NOW, "is_active": is_active,
                "llm_provider": "gemini" if model else None, "model_version": "test-1" if model else None,
                "strategy_prompt": "test" if model else None,
            }],
            returning=("portfolio_id",),
        )
    return row["portfolio_id"]


def _run(db: Database, portfolio_id: int, *, ts: datetime = NOW, status: str | None = "success",
         error: str | None = None) -> int:
    with db.transaction() as tx:
        [row] = tx.repository(ModelRun).insert_returning(
            [{"portfolio_id": portfolio_id, "ts": ts, "config": {}, "llm_provider": "gemini",
              "model_version": "test-1", "status": status, "error_message": error}],
            returning=("run_id",),
        )
    return row["run_id"]


def _decision(db: Database, run_id: int, asset_id: int, *, ts: datetime = NOW, decision: str = "HOLD",
              reasoning: str | None = None) -> None:
    with db.transaction() as tx:
        tx.repository(ModelDecision).insert(
            [{"run_id": run_id, "asset_id": asset_id, "ts": ts, "decision": decision, "reasoning": reasoning,
              "context_snapshot": {}}]
        )


def _prices(db: Database, asset_id: int, points: list[tuple[datetime, float]]) -> None:
    with db.transaction() as tx:
        tx.repository(MarketPrice).insert(
            [{"asset_id": asset_id, "ts": ts, "source": "test", "open": c, "high": c, "low": c, "close": c,
              "volume": 1, "fetched_at": NOW} for ts, c in points]
        )


@pytest.fixture
def full(rollback_full_db: Database) -> Database:
    return rollback_full_db


@pytest.fixture
def ro(rollback_readonly_db: Database) -> Database:
    return rollback_readonly_db


@pytest.fixture
def app(rollback_app_db: Database) -> Database:
    return rollback_app_db


# --- letture -----------------------------------------------------------------------


class TestPortfolios:
    def test_model_benchmark_and_suspended_portfolios(self, full, ro):
        model = _portfolio(full, "test-dash-model")
        bench = _portfolio(full, "test-dash-bench", portfolio_type="benchmark")
        off = _portfolio(full, "test-dash-off", is_active=False)

        with ro.transaction() as tx:
            found = {p.portfolio_id: p for p in DashboardRepository(tx).portfolios()}

        assert {model, bench, off} <= set(found)
        assert found[model].is_model and found[model].model_version == "test-1"
        assert found[bench].portfolio_type == "benchmark"
        assert found[off].is_active is False

    def test_single_portfolio_or_lookup_error(self, full, ro):
        pid = _portfolio(full, "test-dash-one")

        with ro.transaction() as tx:
            repo = DashboardRepository(tx)
            assert repo.portfolio(pid).name == "test-dash-one"
            with pytest.raises(LookupError):
                repo.portfolio(-1)


class TestPositionsAndHistory:
    def test_positions_watchlist_and_history_of_one_portfolio(self, full, ro):
        a, b = _asset(full, "TESTDB1"), _asset(full, "TESTDB2")
        p1, p2 = _portfolio(full, "test-dash-1"), _portfolio(full, "test-dash-2")
        with full.transaction() as tx:
            tx.repository(PortfolioPosition).insert(
                [{"portfolio_id": pid, "asset_id": aid, "quantity": 2.0, "avg_price": 10.0, "updated_at": NOW}
                 for pid, aid in [(p1, a), (p2, b)]]
            )
            tx.repository(PortfolioWatchlistEntry).insert(
                [{"portfolio_id": p1, "asset_id": aid, "added_at": NOW} for aid in (a, b)]
            )
        with full.transaction() as tx:
            tx.repository(Portfolio).update({"cash": 9_980.0}, where={"portfolio_id": p1})
        with full.transaction() as tx:
            tx.repository(PortfolioPosition).delete(where={"portfolio_id": p1, "asset_id": a})

        with ro.transaction() as tx:
            repo = DashboardRepository(tx)
            positions = repo.positions(p1)
            watchlist = repo.watchlist(p1)
            cash = repo.cash_history(p1)
            changes = repo.position_changes(p1)

        assert positions == []
        assert watchlist == ["TESTDB1", "TESTDB2"]
        assert [c.cash for c in cash] == [10_000.0, 9_980.0]
        assert [(c.symbol, c.operation, c.quantity_after) for c in changes] == [
            ("TESTDB1", "INSERT", 2.0),
            ("TESTDB1", "DELETE", 0.0),
        ]

    def test_open_position_with_symbol(self, full, ro):
        a = _asset(full, "TESTDB1")
        pid = _portfolio(full, "test-dash-pos")
        with full.transaction() as tx:
            tx.repository(PortfolioPosition).insert(
                [{"portfolio_id": pid, "asset_id": a, "quantity": 3.0, "avg_price": 5.0, "updated_at": NOW}]
            )

        with ro.transaction() as tx:
            [position] = DashboardRepository(tx).positions(pid)

        assert (position.symbol, position.quantity, position.cost) == ("TESTDB1", 3.0, 15.0)


class TestPrices:
    def test_prices_since_and_latest_per_asset(self, full, ro):
        a, b = _asset(full, "TESTDB1"), _asset(full, "TESTDB2")
        _prices(full, a, [(NOW - timedelta(days=10), 1.0), (NOW - timedelta(days=2), 2.0), (NOW, 3.0)])
        _prices(full, b, [(NOW - timedelta(days=1), 7.0)])

        with ro.transaction() as tx:
            repo = DashboardRepository(tx)
            since = repo.prices([a], since=NOW - timedelta(days=3))
            latest = repo.latest_prices([a, b])

        assert [p.close for p in since] == [2.0, 3.0]
        assert {k: v.close for k, v in latest.items()} == {a: 3.0, b: 7.0}

    def test_no_assets_no_query(self, ro):
        with ro.transaction() as tx:
            repo = DashboardRepository(tx)
            assert repo.prices([], since=NOW) == []
            assert repo.latest_prices([]) == {}

    def test_benchmark_asset_from_universe(self, full, ro):
        _asset(full, "TESTDB_BENCH", benchmark=True)

        with ro.transaction() as tx:
            benchmark = DashboardRepository(tx).benchmark_asset()

        assert benchmark is not None
        assert benchmark[1] in {"SPY", "TESTDB_BENCH"}


class TestDecisionsRunsBacktests:
    def test_decisions_of_the_portfolio_most_recent_first(self, full, ro):
        a = _asset(full, "TESTDB1")
        mine, other = _portfolio(full, "test-dash-mine"), _portfolio(full, "test-dash-other")
        run = _run(full, mine)
        for day, decision in [(1, "HOLD"), (2, "BUY"), (3, "SELL")]:
            _decision(full, run, a, ts=NOW - timedelta(days=10 - day), decision=decision, reasoning=f"r{day}")
        _decision(full, _run(full, other), a, decision="BUY")

        with ro.transaction() as tx:
            decisions = DashboardRepository(tx).decisions(mine, limit=2)

        assert [(d.decision, d.symbol, d.reasoning) for d in decisions] == [("SELL", "TESTDB1", "r3"),
                                                                             ("BUY", "TESTDB1", "r2")]

    def test_runs_with_outcome_and_decision_count(self, full, ro):
        a, b = _asset(full, "TESTDB1"), _asset(full, "TESTDB2")
        pid = _portfolio(full, "test-dash-runs")
        old = _run(full, pid, ts=NOW - timedelta(days=7), status=None)
        failed = _run(full, pid, ts=NOW, status="failed", error="429 quota")
        _decision(full, failed, a)
        _decision(full, failed, b)

        with ro.transaction() as tx:
            runs = DashboardRepository(tx).runs(pid)

        assert [(r.run_id, r.status, r.decisions) for r in runs] == [(failed, "failed", 2), (old, None, 0)]
        assert runs[0].error_message == "429 quota"

    def test_backtests_of_the_portfolio(self, full, ro):
        pid, other = _portfolio(full, "test-dash-bt"), _portfolio(full, "test-dash-bt-other")
        run, other_run = _run(full, pid), _run(full, other)
        with full.transaction() as tx:
            tx.repository(BacktestResult).insert(
                [{"run_id": r, "pnl": pnl, "sharpe_ratio": None, "max_drawdown": -0.1, "win_rate": 0.5,
                  "period_start": date(2026, 10, 1), "period_end": date(2026, 10, 2)}
                 for r, pnl in [(run, 12.5), (other_run, 99.0)]]
            )

        with ro.transaction() as tx:
            [result] = DashboardRepository(tx).backtests(pid)

        assert (result.run_id, result.pnl, result.sharpe_ratio) == (run, 12.5, None)


class TestIngestionHealth:
    def test_latest_run_per_pipeline(self, full, ro):
        with full.transaction() as tx:
            tx.repository(IngestionRun).insert(
                [{"source": "fred", "target_table": target, "started_at": NOW - timedelta(hours=h),
                  "status": status, "error_message": err}
                 for target, h, status, err in [
                     ("test_dash.t_a", 3, "success", None),
                     ("test_dash.t_a", 1, "failed", "boom"),
                     ("test_dash.t_b", 2, "partial", None),
                 ]]
            )

        with ro.transaction() as tx:
            latest = [r for r in DashboardRepository(tx).latest_ingestion_runs() if r.target_table.startswith("test_dash.")]

        assert [(r.target_table, r.status, r.error_message) for r in latest] == [
            ("test_dash.t_a", "failed", "boom"),
            ("test_dash.t_b", "partial", None),
        ]


class TestReadOnlyPolicy:
    def test_dashboard_reader_cannot_write(self, full, ro):
        pid = _portfolio(full, "test-dash-ro")

        with ro.transaction() as tx:
            with pytest.raises(AccessDeniedError):
                tx.repository(Portfolio).update({"is_active": False}, where={"portfolio_id": pid})


# --- gestione ----------------------------------------------------------------------


def _spec(**overrides) -> NewModelPortfolio:
    values = dict(name="test-dash-new", starting_capital=5_000.0, llm_provider="gemini",
                  model_version="gemini-test", strategy_prompt="compra qualità", is_active=False)
    values.update(overrides)
    return NewModelPortfolio(**values)


class TestPortfolioAdmin:
    def test_create_model_portfolio(self, app, ro):
        with app.transaction() as tx:
            pid = PortfolioAdmin(tx).create_model_portfolio(_spec(), now=NOW)

        with ro.transaction() as tx:
            repo = DashboardRepository(tx)
            created = repo.portfolio(pid)
            history = repo.cash_history(pid)

        assert (created.name, created.portfolio_type, created.is_active) == ("test-dash-new", "model", False)
        assert (created.cash, created.starting_capital) == (5_000.0, 5_000.0)
        assert created.strategy_prompt == "compra qualità"
        assert created.next_decision_at is None
        assert [h.cash for h in history] == [5_000.0]

    def test_duplicate_name_rejected(self, full, app):
        _portfolio(full, "test-dash-dup")

        with app.transaction() as tx:
            with pytest.raises(ValueError, match="esiste già"):
                PortfolioAdmin(tx).create_model_portfolio(_spec(name="test-dash-dup"), now=NOW)

    def test_suspend_and_reactivate(self, full, app, ro):
        pid = _portfolio(full, "test-dash-toggle")

        with app.transaction() as tx:
            PortfolioAdmin(tx).set_active(pid, False)
        with ro.transaction() as tx:
            assert DashboardRepository(tx).portfolio(pid).is_active is False

        with app.transaction() as tx:
            PortfolioAdmin(tx).set_active(pid, True)
        with ro.transaction() as tx:
            assert DashboardRepository(tx).portfolio(pid).is_active is True

    def test_request_new_cycle_makes_the_portfolio_due(self, full, app, ro):
        pid = _portfolio(full, "test-dash-cycle")

        with app.transaction() as tx:
            PortfolioAdmin(tx).request_new_cycle(pid, now=NOW)
        with ro.transaction() as tx:
            assert DashboardRepository(tx).portfolio(pid).next_decision_at == NOW

    def test_request_new_cycle_rejects_benchmark(self, full, app):
        bench = _portfolio(full, "test-dash-cycle-bench", portfolio_type="benchmark")

        with app.transaction() as tx:
            with pytest.raises(ValueError, match="model"):
                PortfolioAdmin(tx).request_new_cycle(bench, now=NOW)

    def test_update_settings(self, full, app, ro):
        pid = _portfolio(full, "test-dash-settings")

        with app.transaction() as tx:
            PortfolioAdmin(tx).update_settings(pid, model_version="gemini-next", strategy_prompt="solo value")
        with ro.transaction() as tx:
            updated = DashboardRepository(tx).portfolio(pid)

        assert (updated.model_version, updated.strategy_prompt) == ("gemini-next", "solo value")

    def test_unknown_portfolio(self, app):
        with app.transaction() as tx:
            with pytest.raises(LookupError):
                PortfolioAdmin(tx).set_active(-1, True)
