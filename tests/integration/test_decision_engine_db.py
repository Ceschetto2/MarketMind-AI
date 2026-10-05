"""Test di integrazione del Decision Engine contro Postgres reale:
repository, `ContextBuilder`, `PostgresDecisionStore` e il motore end-to-end
con un provider finto.

Tutto gira sulla transazione annullata di `rollback_connection`: i dati di
prova (asset `TESTDE*`, portfolio `test-de-*`) sono preparati con
`rollback_full_db` e il codice sotto test usa `rollback_app_db`, cioè la
policy del ruolo `marketmind_app`. Nulla resta nel DB a fine test.
Riprende i casi dei vecchi test di `context_reader`, `portfolio_reader`/
`portfolio_writer`, `decision_writer` e `backtest_reader`/`backtest_writer`,
che scrivevano dati reali con pulizia manuale.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from marketmind_db.database import Database
from marketmind_db.exceptions import AccessDeniedError
from marketmind_db.models.decisions import BacktestResult, ModelDecision, ModelRun
from marketmind_db.models.market_data import (
    Asset,
    CompanyEvent,
    MacroEvent,
    MarketPrice,
    NewsEvent,
    UniverseMember,
)
from marketmind_db.models.portfolio import (
    Portfolio,
    PortfolioPosition,
    PortfolioPositionSnapshot,
    PortfolioSnapshot,
)
from marketmind_llm_decision_engine.backtest.engine import Backtester, PostgresBacktestStore
from marketmind_llm_decision_engine.decision_engine.context_builder import ContextBuilder, ContextWindows
from marketmind_llm_decision_engine.decision_engine.engine import DecisionEngine
from marketmind_llm_decision_engine.decision_engine.store import PostgresDecisionStore, TradeRequest
from marketmind_llm_decision_engine.decision_engine.trading import PositionState, plan_trade
from marketmind_llm_decision_engine.llm.exceptions import DecisionError
from marketmind_llm_decision_engine.llm.schemas import Decision, WatchlistSelection
from marketmind_llm_decision_engine.repositories import (
    BacktestRepository,
    DecisionRepository,
    MarketContextRepository,
    PortfolioRepository,
)

pytestmark = pytest.mark.integration

NOW = datetime.now(timezone.utc).replace(microsecond=0)


# --- preparazione dei dati -------------------------------------------------------


def _asset(db: Database, symbol: str, *, benchmark: bool | None = False) -> int:
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
               is_active: bool = True, next_decision_at: datetime | None = None) -> int:
    model = portfolio_type == "model"
    with db.transaction() as tx:
        [row] = tx.repository(Portfolio).insert_returning(
            [{
                "name": name, "portfolio_type": portfolio_type, "starting_capital": cash, "cash": cash,
                "equity_value": cash, "created_at": NOW, "is_active": is_active,
                "llm_provider": "fake" if model else None, "model_version": "fake-1" if model else None,
                "strategy_prompt": "test" if model else None, "next_decision_at": next_decision_at,
            }],
            returning=("portfolio_id",),
        )
    return row["portfolio_id"]


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
def app(rollback_app_db: Database) -> Database:
    return rollback_app_db


# --- MarketContextRepository -----------------------------------------------------


class TestMarketContext:
    def test_prezzi_nella_finestra_e_solo_dellasset(self, full, app):
        a, b = _asset(full, "TESTDE1"), _asset(full, "TESTDE2")
        _prices(full, a, [(NOW - timedelta(days=40), 1.0), (NOW - timedelta(days=2), 2.0), (NOW - timedelta(hours=1), 3.0),
                          (NOW + timedelta(hours=1), 99.0)])
        _prices(full, b, [(NOW - timedelta(days=1), 50.0)])

        with app.transaction() as tx:
            prices = MarketContextRepository(tx).prices(a, as_of=NOW, days_back=30)

        assert [p.close for p in prices] == [2.0, 3.0]

    def test_news_finestra_cap_e_piu_recenti_prima(self, full, app):
        a, b = _asset(full, "TESTDE1"), _asset(full, "TESTDE2")
        with full.transaction() as tx:
            tx.repository(NewsEvent).insert(
                [{"asset_id": asset, "source": "test", "ts": NOW - timedelta(days=d), "headline": f"n{d}",
                  "url": f"https://example.test/de/{asset}/{d}", "fetched_at": NOW}
                 for asset, d in [(a, 1), (a, 2), (a, 3), (a, 10), (b, 1)]]
            )

        with app.transaction() as tx:
            news = MarketContextRepository(tx).news(a, as_of=NOW, days_back=7, max_items=2)

        assert [n.headline for n in news] == ["n1", "n2"]

    def test_eventi_societari_nella_finestra(self, full, app):
        a = _asset(full, "TESTDE1")
        with full.transaction() as tx:
            tx.repository(CompanyEvent).insert(
                [{"asset_id": a, "ts": NOW.date() - timedelta(days=d), "event_type": "earnings", "source": "test",
                  "fetched_at": NOW} for d in (5, 100)]
            )

        with app.transaction() as tx:
            events = MarketContextRepository(tx).company_events(a, as_of=NOW, days_back=90)

        assert [e.ts for e in events] == [NOW.date() - timedelta(days=5)]

    def test_macro_ultimo_valore_noto_senza_look_ahead(self, full, app):
        with full.transaction() as tx:
            tx.repository(MacroEvent).insert(
                [{"indicator": "TESTDE_IND", "ts": ts, "value": v, "source": "test", "fetched_at": NOW}
                 for ts, v in [(date(2026, 7, 1), 1.0), (date(2026, 8, 1), 2.0), (date(2026, 9, 1), 3.0)]]
            )

        with app.transaction() as tx:
            macro = MarketContextRepository(tx).latest_macro(as_of=datetime(2026, 8, 15, tzinfo=timezone.utc))

        assert [m.value for m in macro if m.indicator == "TESTDE_IND"] == [2.0]

    def test_universo_decisionale_esclude_benchmark_e_non_membri(self, full, app):
        _asset(full, "TESTDE1"), _asset(full, "TESTDE_BENCH", benchmark=True), _asset(full, "TESTDE_OUT", benchmark=None)

        with app.transaction() as tx:
            symbols = {a.symbol for a in MarketContextRepository(tx).decision_universe()}

        assert "TESTDE1" in symbols
        assert not {"TESTDE_BENCH", "TESTDE_OUT"} & symbols


# --- PortfolioRepository ---------------------------------------------------------


class TestPortfolio:
    def test_portfolio_inesistente(self, app):
        with app.transaction() as tx:
            with pytest.raises(LookupError):
                PortfolioRepository(tx).get(-1)

    def test_portfolio_dovuti(self, full, app):
        due_null = _portfolio(full, "test-de-null")
        due_past = _portfolio(full, "test-de-past", next_decision_at=NOW - timedelta(hours=1))
        future = _portfolio(full, "test-de-future", next_decision_at=NOW + timedelta(days=1))
        inactive = _portfolio(full, "test-de-inactive", is_active=False)
        bench = _portfolio(full, "test-de-bench", portfolio_type="benchmark")

        with app.transaction() as tx:
            due = {p.portfolio_id for p in PortfolioRepository(tx).due_model_portfolios(NOW)}

        assert {due_null, due_past} <= due
        assert not {future, inactive, bench} & due

    def test_posizioni_e_watchlist_solo_del_portfolio(self, full, app):
        a, b = _asset(full, "TESTDE1"), _asset(full, "TESTDE2")
        p1, p2 = _portfolio(full, "test-de-1"), _portfolio(full, "test-de-2")
        with full.transaction() as tx:
            tx.repository(PortfolioPosition).insert(
                [{"portfolio_id": pid, "asset_id": aid, "quantity": 1.0, "avg_price": 10.0, "updated_at": NOW}
                 for pid, aid in [(p1, a), (p2, b)]]
            )
        with app.transaction() as tx:
            PortfolioRepository(tx).replace_watchlist(p1, [b, a, b], added_at=NOW)

        with app.transaction() as tx:
            repo = PortfolioRepository(tx)
            positions = repo.positions(p1)
            watchlist = repo.watchlist(p1)
            other = repo.watchlist(p2)

        assert [(p.symbol, p.quantity) for p in positions] == [("TESTDE1", 1.0)]
        assert [w.symbol for w in watchlist] == ["TESTDE1", "TESTDE2"]
        assert other == []

    def test_replace_watchlist_sostituisce_e_puo_svuotare(self, full, app):
        a, b = _asset(full, "TESTDE1"), _asset(full, "TESTDE2")
        p = _portfolio(full, "test-de-1")
        with app.transaction() as tx:
            repo = PortfolioRepository(tx)
            repo.replace_watchlist(p, [a, b], added_at=NOW)
            repo.replace_watchlist(p, [b], added_at=NOW)
            after_replace = [w.symbol for w in repo.watchlist(p)]
            repo.replace_watchlist(p, [], added_at=NOW)
            after_clear = repo.watchlist(p)

        assert after_replace == ["TESTDE2"]
        assert after_clear == []

    def test_schedule_next_decision(self, full, app):
        p = _portfolio(full, "test-de-1")
        with app.transaction() as tx:
            PortfolioRepository(tx).schedule_next_decision(p, NOW + timedelta(days=7))
            assert PortfolioRepository(tx).get(p).next_decision_at == NOW + timedelta(days=7)

    def test_apply_trade_crea_aggiorna_e_chiude_la_posizione(self, full, app):
        a = _asset(full, "TESTDE1")
        p = _portfolio(full, "test-de-1", cash=1_000.0)
        steps = [("BUY", 0.5, 100.0), ("BUY", 0.5, 200.0), ("SELL", 1.0, 150.0)]
        seen = []
        with app.transaction() as tx:
            repo = PortfolioRepository(tx)
            for action, size, price in steps:
                plan = plan_trade(cash=repo.get(p).cash, position=repo.position(p, a), action=action,
                                  size_pct=size, price=price)
                repo.apply_trade(p, a, plan)
                seen.append((round(repo.get(p).cash, 6), repo.position(p, a)))

        assert seen[0] == (500.0, PositionState(quantity=5.0, avg_price=100.0))
        # 250 di cash a 200 = 1,25 quote; media (5*100 + 1,25*200) / 6,25 = 120
        assert seen[1][0] == 250.0 and seen[1][1].quantity == pytest.approx(6.25)
        assert seen[1][1].avg_price == pytest.approx(120.0)
        assert seen[2] == (pytest.approx(250.0 + 6.25 * 150.0), None)

    def test_policy_app_non_scrive_market_data(self, app):
        with app.transaction() as tx:
            with pytest.raises(AccessDeniedError, match="market_data"):
                tx.repository(Asset).insert(
                    [{"symbol": "TESTDE_X", "name": "x", "asset_type": "equity", "source": "t", "fetched_at": NOW}]
                )

    def test_cancellare_un_portfolio_non_fallisce(self, full):
        """Regressione di `0013`: il trigger AFTER DELETE di `t_portfolios`
        generava una riga di storico che referenziava la riga appena
        cancellata, e faceva fallire ogni DELETE. Lo storico già esistente (qui
        prodotto dall'INSERT stesso) va comunque rimosso prima, come per
        qualunque FK."""
        p = _portfolio(full, "test-de-delete")
        with full.transaction() as tx:
            tx.repository(PortfolioSnapshot).delete(where={"portfolio_id": p})
            assert tx.repository(Portfolio).delete(where={"portfolio_id": p}) == 1
            assert tx.repository(Portfolio).get_one(portfolio_id=p) is None


# --- DecisionRepository e PostgresDecisionStore -----------------------------------


def _decision(action: str = "HOLD", size: float | None = None) -> Decision:
    return Decision(decision=action, size_pct=size, confidence=0.7, reasoning="test")


class TestDecisions:
    def test_run_nasce_running_e_si_chiude_con_esito(self, full, app):
        p = _portfolio(full, "test-de-1")
        with app.transaction() as tx:
            repo = DecisionRepository(tx)
            run_id = repo.create_run(portfolio_id=p, ts=NOW, config={"w": 1}, llm_provider="fake", model_version="m")
            created = tx.repository(ModelRun).get_one(run_id=run_id)
            status_created = created.status
            repo.finish_run(run_id, "partial", "x" * 3_000)
            finished = tx.repository(ModelRun).get_one(run_id=run_id)

        assert status_created == "running"
        assert finished.status == "partial" and finished.finished_at is not None
        assert len(finished.error_message) == 2_000

    def test_decisione_hold_senza_size(self, full, app):
        a, p = _asset(full, "TESTDE1"), _portfolio(full, "test-de-1")
        with app.transaction() as tx:
            repo = DecisionRepository(tx)
            run_id = repo.create_run(portfolio_id=p, ts=NOW, config={}, llm_provider="fake", model_version="m")
            decision_id = repo.write_decision(run_id=run_id, asset_id=a, ts=NOW, decision=_decision(),
                                              context_snapshot={"k": "v"})
            row = tx.repository(ModelDecision).get_one(decision_id=decision_id)

        assert (row.decision, row.size_pct, row.context_snapshot) == ("HOLD", None, {"k": "v"})

    def test_decisione_e_trade_nella_stessa_transazione_collegati_al_run(self, full, app):
        a, p = _asset(full, "TESTDE1"), _portfolio(full, "test-de-1", cash=1_000.0)
        store = PostgresDecisionStore(app)
        portfolio = store.portfolio(p)
        run_id = store.start_run(portfolio, as_of=NOW)
        asset = next(x for x in store.decision_universe() if x.symbol == "TESTDE1")

        changed = store.record_decision(
            run_id=run_id, portfolio_id=p, asset=asset, as_of=NOW, decision=_decision("BUY", 0.1),
            context={"symbol": "TESTDE1"}, trade=TradeRequest(action="BUY", size_pct=0.1, price=50.0),
        )

        with full.session() as session:
            cash = session.get(Portfolio, p).cash
            position_runs = session.execute(
                select(PortfolioPositionSnapshot.run_id).where(PortfolioPositionSnapshot.portfolio_id == p)
            ).scalars().all()
            cash_runs = session.execute(
                select(PortfolioSnapshot.run_id).where(PortfolioSnapshot.portfolio_id == p, PortfolioSnapshot.cash == 900.0)
            ).scalars().all()
            decisions = session.execute(select(ModelDecision).where(ModelDecision.run_id == run_id)).scalars().all()

        assert changed and cash == pytest.approx(900.0)
        assert position_runs == [run_id] and cash_runs == [run_id]
        assert [d.decision for d in decisions] == ["BUY"]


# --- ContextBuilder ----------------------------------------------------------------


class TestContextBuilder:
    def test_contesto_con_stato_del_solo_portfolio_e_finestre(self, full, app):
        a, other_asset = _asset(full, "TESTDE1"), _asset(full, "TESTDE2")
        p, other = _portfolio(full, "test-de-1", cash=1_234.0), _portfolio(full, "test-de-2")
        _prices(full, a, [(NOW - timedelta(days=5), 10.0), (NOW - timedelta(days=1), 11.0)])
        with full.transaction() as tx:
            tx.repository(PortfolioPosition).insert(
                [{"portfolio_id": pid, "asset_id": aid, "quantity": 2.0, "avg_price": 9.0, "updated_at": NOW}
                 for pid, aid in [(p, a), (other, other_asset)]]
            )

        with app.transaction() as tx:
            asset = next(x for x in MarketContextRepository(tx).decision_universe() if x.symbol == "TESTDE1")
            context = ContextBuilder(tx, ContextWindows(price_days_back=3)).build(asset, portfolio_id=p, as_of=NOW)

        assert context.symbol == "TESTDE1" and context.portfolio.cash == 1_234.0
        assert [x.symbol for x in context.portfolio.positions] == ["TESTDE1"]
        assert [pp.close for pp in context.prices] == [11.0]

    def test_contesto_di_bootstrap_universo_e_strategia(self, full, app):
        _asset(full, "TESTDE1")
        p = _portfolio(full, "test-de-1")
        with app.transaction() as tx:
            context = ContextBuilder(tx).build_bootstrap(p)

        assert context.strategy_prompt == "test"
        assert "TESTDE1" in {a.symbol for a in context.universe}


# --- BacktestRepository ------------------------------------------------------------


class TestBacktestRepository:
    def test_eventi_del_solo_run_e_risultato(self, full, app):
        a, p = _asset(full, "TESTDE1"), _portfolio(full, "test-de-1", cash=1_000.0)
        store = PostgresDecisionStore(app)
        portfolio = store.portfolio(p)
        asset = next(x for x in store.decision_universe() if x.symbol == "TESTDE1")
        runs = [store.start_run(portfolio, as_of=NOW) for _ in range(2)]
        for run_id, size in zip(runs, (0.1, 0.2)):
            store.record_decision(run_id=run_id, portfolio_id=p, asset=asset, as_of=NOW,
                                  decision=_decision("BUY", size), context={},
                                  trade=TradeRequest(action="BUY", size_pct=size, price=50.0))

        with app.transaction() as tx:
            repo = BacktestRepository(tx)
            events = repo.position_events(p, runs[0])
            empty = repo.position_events(p, -1)
            backtest_id = repo.write_result(run_id=runs[0], pnl=1.5, sharpe_ratio=None, max_drawdown=None,
                                            win_rate=None, period_start=NOW.date(), period_end=NOW.date())

        assert [(e.symbol, e.operation, e.quantity) for e in events] == [("TESTDE1", "INSERT", pytest.approx(2.0))]
        assert empty == []
        assert backtest_id > 0


class TestBacktesterEndToEnd:
    def test_backtest_di_un_run_reale_scrive_il_risultato(self, full, app):
        a, p = _asset(full, "TESTDE1"), _portfolio(full, "test-de-1", cash=1_000.0)
        # L'ora del trade nello snapshot è il now() della transazione (circa
        # NOW): una barra prima, una dopo.
        _prices(full, a, [(NOW - timedelta(hours=1), 50.0), (NOW + timedelta(hours=1), 60.0)])
        store = PostgresDecisionStore(app)
        run_id = store.start_run(store.portfolio(p), as_of=NOW)
        asset = next(x for x in store.decision_universe() if x.symbol == "TESTDE1")
        store.record_decision(run_id=run_id, portfolio_id=p, asset=asset, as_of=NOW,
                              decision=_decision("BUY", 0.5), context={},
                              trade=TradeRequest(action="BUY", size_pct=0.5, price=50.0))

        metrics, backtest_id = Backtester(PostgresBacktestStore(app)).run(
            portfolio_id=p, run_id=run_id, as_of=NOW + timedelta(hours=2)
        )

        with full.transaction() as tx:
            row = tx.repository(BacktestResult).get_one(backtest_id=backtest_id)
        # 500 investiti a 50 = 10 quote; ultimo prezzo 60 -> +100
        assert metrics.pnl == pytest.approx(100.0)
        assert row.run_id == run_id and row.pnl == pytest.approx(100.0)


# --- DecisionEngine end-to-end ---------------------------------------------------------


class FakeProvider:
    def __init__(self, action="BUY", fail=False):
        self.action, self.fail = action, fail

    def decide(self, context):
        if self.fail:
            raise DecisionError("500 INTERNAL")
        return _decision(self.action, None if self.action == "HOLD" else 0.5)

    def select_watchlist(self, context):
        return WatchlistSelection(symbols=["TESTDE1", "TESTDE2"], reasoning="test")


class TestEngineEndToEnd:
    def _setup(self, full):
        a, b = _asset(full, "TESTDE1"), _asset(full, "TESTDE2")
        _prices(full, a, [(NOW - timedelta(hours=2), 100.0)])
        _prices(full, b, [(NOW - timedelta(days=6), 100.0)])  # troppo vecchio per un trade
        return _portfolio(full, "test-de-e2e", cash=1_000.0)

    def _run(self, app, p, provider):
        engine = DecisionEngine(PostgresDecisionStore(app), lambda name, model: provider, clock=lambda: NOW)
        results = engine.run_due(as_of=NOW)
        return next(r for r in results if r.portfolio_id == p)

    def test_bootstrap_poi_giro_dovuto(self, full, app):
        p = self._setup(full)
        engine = DecisionEngine(PostgresDecisionStore(app), lambda name, model: FakeProvider(), clock=lambda: NOW)

        result = engine.initialize_portfolio(p)

        with full.session() as session:
            portfolio = session.get(Portfolio, p)
            run = session.get(ModelRun, result.run_id)
            positions = session.execute(select(PortfolioPosition).where(PortfolioPosition.portfolio_id == p)).scalars().all()
        assert (run.status, result.decisions_written, result.trades_executed) == ("success", 2, 1)
        assert portfolio.cash == pytest.approx(500.0)
        assert len(positions) == 1
        assert portfolio.next_decision_at == NOW + timedelta(days=7)

    def test_giro_tutto_fallito_resta_dovuto(self, full, app):
        p = self._setup(full)
        with app.transaction() as tx:
            PortfolioRepository(tx).replace_watchlist(p, [
                a.asset_id for a in MarketContextRepository(tx).decision_universe() if a.symbol.startswith("TESTDE")
            ], added_at=NOW)

        result = self._run(app, p, FakeProvider(fail=True))

        with full.session() as session:
            run = session.get(ModelRun, result.run_id)
            portfolio = session.get(Portfolio, p)
        assert run.status == "failed" and "2/2 decisioni fallite" in run.error_message
        assert portfolio.next_decision_at is None
