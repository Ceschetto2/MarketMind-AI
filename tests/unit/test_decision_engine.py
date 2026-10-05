"""Test unitari di `DecisionEngine` (`decision_engine/engine.py`).

Nessun DB, nessuna patch globale: il motore riceve uno store in memoria
(`FakeStore`, che implementa `DecisionStore`) e una factory di provider
finti. I casi riprendono il vecchio `test_engine.py` più quelli nuovi:
esito del run, cadenza dopo un giro fallito, prezzo di esecuzione vecchio.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from marketmind_llm_decision_engine.decision_engine.context_builder import ContextWindows
from marketmind_llm_decision_engine.decision_engine.engine import DecisionEngine
from marketmind_llm_decision_engine.decision_engine.schemas import (
    AssetSummary,
    BootstrapContext,
    DecisionContext,
    PortfolioState,
    PricePoint,
)
from marketmind_llm_decision_engine.llm.exceptions import DecisionError
from marketmind_llm_decision_engine.llm.schemas import Decision, WatchlistSelection
from marketmind_llm_decision_engine.repositories.market_context import AssetRef
from marketmind_llm_decision_engine.repositories.portfolio import PortfolioRef

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
INTERVAL = timedelta(days=7)

AAPL = AssetRef(1, "AAPL", "Apple Inc.", "Technology", "equity")
MSFT = AssetRef(2, "MSFT", "Microsoft Corporation", "Technology", "equity")
NVDA = AssetRef(3, "NVDA", "NVIDIA Corporation", "Technology", "equity")
UNIVERSE = [AAPL, MSFT, NVDA]

ALPHA = PortfolioRef(10, "alpha", "fake", "model-a")
BETA = PortfolioRef(20, "beta", "fake", "model-b")


@dataclass
class RecordedDecision:
    run_id: int
    portfolio_id: int
    symbol: str
    decision: str
    trade: object
    context: dict


@dataclass
class FakeStore:
    due: list[PortfolioRef] = field(default_factory=list)
    watchlists: dict[int, list[AssetRef]] = field(default_factory=dict)
    prices: dict[str, list[PricePoint]] = field(default_factory=dict)
    windows: ContextWindows = ContextWindows()
    runs: dict[int, dict] = field(default_factory=dict)
    decisions: list[RecordedDecision] = field(default_factory=list)
    scheduled: dict[int, datetime] = field(default_factory=dict)
    replaced: dict[int, list[int]] = field(default_factory=dict)
    fail_watchlist_for: set[int] = field(default_factory=set)

    def due_portfolios(self, as_of):
        return list(self.due)

    def portfolio(self, portfolio_id):
        return next(p for p in (ALPHA, BETA) if p.portfolio_id == portfolio_id)

    def watchlist(self, portfolio_id):
        if portfolio_id in self.fail_watchlist_for:
            raise RuntimeError("DB giù")
        return list(self.watchlists.get(portfolio_id, []))

    def decision_universe(self):
        return list(UNIVERSE)

    def decision_context(self, asset, *, portfolio_id, as_of):
        return DecisionContext(
            asset_id=asset.asset_id,
            symbol=asset.symbol,
            as_of=as_of,
            portfolio=PortfolioState(portfolio_id=portfolio_id, name="p", cash=1_000.0, strategy_prompt="s", positions=[]),
            prices=self.prices.get(asset.symbol, [PricePoint(ts=as_of - timedelta(hours=1), close=100.0)]),
            news=[],
            macro_events=[],
            company_events=[],
        )

    def bootstrap_context(self, portfolio_id):
        return BootstrapContext(
            portfolio_id=portfolio_id,
            portfolio_name="p",
            strategy_prompt="s",
            universe=[AssetSummary(symbol=a.symbol, name=a.name, sector=a.sector, asset_type=a.asset_type) for a in UNIVERSE],
        )

    def replace_watchlist(self, portfolio_id, asset_ids, *, added_at):
        self.replaced[portfolio_id] = list(asset_ids)
        self.watchlists[portfolio_id] = [a for a in UNIVERSE if a.asset_id in asset_ids]

    def start_run(self, portfolio, *, as_of):
        run_id = len(self.runs) + 1
        self.runs[run_id] = {"portfolio": portfolio, "as_of": as_of, "status": "running", "error": None}
        return run_id

    def finish_run(self, run_id, status, error_message=None):
        self.runs[run_id].update(status=status, error=error_message)

    def record_decision(self, *, run_id, portfolio_id, asset, as_of, decision, context, trade):
        self.decisions.append(RecordedDecision(run_id, portfolio_id, asset.symbol, decision.decision, trade, context))
        return trade is not None

    def schedule_next_decision(self, portfolio_id, at):
        self.scheduled[portfolio_id] = at


class FakeProvider:
    def __init__(self, decisions=None, failing=(), selection=("AAPL", "MSFT")):
        self.decisions = decisions or {}
        self.failing = set(failing)
        self.selection = list(selection)
        self.calls: list[str] = []

    def decide(self, context):
        symbol = context["symbol"]
        self.calls.append(symbol)
        if symbol in self.failing:
            raise DecisionError(f"500 INTERNAL per {symbol}")
        action = self.decisions.get(symbol, "HOLD")
        return Decision(decision=action, size_pct=None if action == "HOLD" else 0.25, reasoning="r")

    def select_watchlist(self, context):
        return WatchlistSelection(symbols=self.selection, reasoning="r")


class Factory:
    def __init__(self, providers):
        self.providers = providers
        self.requested: list[tuple[str, str]] = []

    def __call__(self, name, model):
        self.requested.append((name, model))
        provider = self.providers[model]
        if isinstance(provider, Exception):
            raise provider
        return provider


def _engine(store, providers, **kwargs):
    factory = Factory(providers)
    engine = DecisionEngine(store, factory, clock=lambda: NOW, interval=INTERVAL, **kwargs)
    return engine, factory


class TestRunDue:
    def test_nessun_portfolio_dovuto(self, caplog):
        caplog.set_level(logging.INFO)
        store = FakeStore()
        engine, _ = _engine(store, {})

        assert engine.run_due() == []
        assert store.runs == {}
        assert "nessun portfolio dovuto" in caplog.text

    def test_una_decisione_per_asset_per_portfolio_con_provider_proprio(self):
        store = FakeStore(due=[ALPHA, BETA], watchlists={10: [AAPL, MSFT], 20: [NVDA]})
        alpha, beta = FakeProvider(), FakeProvider()
        engine, factory = _engine(store, {"model-a": alpha, "model-b": beta})

        results = engine.run_due()

        assert factory.requested == [("fake", "model-a"), ("fake", "model-b")]
        assert alpha.calls == ["AAPL", "MSFT"] and beta.calls == ["NVDA"]
        assert [(d.portfolio_id, d.symbol) for d in store.decisions] == [(10, "AAPL"), (10, "MSFT"), (20, "NVDA")]
        assert [r.status for r in results] == ["success", "success"]
        assert {r["status"] for r in store.runs.values()} == {"success"}

    def test_run_per_portfolio_e_contesto_del_portfolio_giusto(self):
        store = FakeStore(due=[ALPHA], watchlists={10: [AAPL]})
        engine, _ = _engine(store, {"model-a": FakeProvider()})

        engine.run_due()

        assert store.runs[1]["portfolio"] == ALPHA and store.runs[1]["as_of"] == NOW
        assert store.decisions[0].context["portfolio"]["portfolio_id"] == 10

    def test_watchlist_vuota_crea_un_run_senza_decisioni(self):
        store = FakeStore(due=[ALPHA], watchlists={10: []})
        engine, _ = _engine(store, {"model-a": FakeProvider()})

        [result] = engine.run_due()

        assert (result.status, result.decisions_total) == ("success", 0)
        assert store.decisions == [] and len(store.runs) == 1
        assert store.scheduled[10] == NOW + INTERVAL


class TestEsitoECadenza:
    def test_una_decisione_fallita_e_partial_e_schedula(self):
        store = FakeStore(due=[ALPHA], watchlists={10: [AAPL, MSFT, NVDA]})
        engine, _ = _engine(store, {"model-a": FakeProvider(failing={"MSFT"})})

        [result] = engine.run_due()

        assert result.status == "partial"
        assert [d.symbol for d in store.decisions] == ["AAPL", "NVDA"]
        assert store.runs[1]["status"] == "partial"
        assert "1/3 decisioni fallite" in store.runs[1]["error"] and "500 INTERNAL" in store.runs[1]["error"]
        assert store.scheduled[10] == NOW + INTERVAL

    def test_tutte_fallite_e_failed_e_non_schedula(self):
        """Il caso del 1 ottobre (Gemma in errore): prima il portfolio perdeva
        una settimana in silenzio."""
        store = FakeStore(due=[ALPHA], watchlists={10: [AAPL, MSFT]})
        engine, _ = _engine(store, {"model-a": FakeProvider(failing={"AAPL", "MSFT"})})

        [result] = engine.run_due()

        assert result.status == "failed"
        assert store.runs[1]["status"] == "failed"
        assert 10 not in store.scheduled

    def test_errore_su_un_portfolio_non_blocca_gli_altri(self):
        store = FakeStore(due=[ALPHA, BETA], watchlists={10: [AAPL], 20: [NVDA]}, fail_watchlist_for={10})
        engine, _ = _engine(store, {"model-a": FakeProvider(), "model-b": FakeProvider()})

        results = engine.run_due()

        assert [(r.portfolio_id, r.status) for r in results] == [(10, "failed"), (20, "success")]
        assert [d.symbol for d in store.decisions] == ["NVDA"]
        assert list(store.scheduled) == [20]

    def test_provider_non_costruibile_e_failed_senza_run(self):
        store = FakeStore(due=[ALPHA, BETA], watchlists={10: [AAPL], 20: [NVDA]})
        engine, _ = _engine(store, {"model-a": ValueError("provider sconosciuto"), "model-b": FakeProvider()})

        results = engine.run_due()

        assert results[0].status == "failed" and results[0].run_id is None
        assert results[1].status == "success"

    def test_errore_dello_store_durante_il_giro_chiude_il_run_failed(self):
        class BrokenStore(FakeStore):
            def record_decision(self, **kwargs):
                raise RuntimeError("scrittura fallita")

        store = BrokenStore(due=[ALPHA], watchlists={10: [AAPL]})
        engine, _ = _engine(store, {"model-a": FakeProvider()})

        [result] = engine.run_due()

        assert result.status == "failed"
        assert store.runs[1]["status"] == "failed"
        assert 10 not in store.scheduled


class TestInterruzione:
    def test_interruzione_chiude_il_run_failed_e_si_propaga(self):
        """Un SIGTERM (BaseException) non è l'errore di una decisione: deve
        chiudere il run `failed` invece di lasciarlo `running`, e propagarsi."""

        class Stop(BaseException):
            pass

        class Interrupting(FakeProvider):
            def decide(self, context):
                raise Stop()

        store = FakeStore(due=[ALPHA], watchlists={10: [AAPL, MSFT]})
        engine, _ = _engine(store, {"model-a": Interrupting()})

        with pytest.raises(Stop):
            engine.run_due()
        assert store.runs[1]["status"] == "failed"
        assert "interrotto" in store.runs[1]["error"]
        assert 10 not in store.scheduled


class TestTrade:
    def test_buy_e_sell_chiedono_un_trade_allultimo_prezzo(self):
        store = FakeStore(
            due=[ALPHA],
            watchlists={10: [AAPL, MSFT]},
            prices={"AAPL": [PricePoint(ts=NOW - timedelta(hours=3), close=90.0), PricePoint(ts=NOW - timedelta(hours=1), close=95.0)]},
        )
        engine, _ = _engine(store, {"model-a": FakeProvider(decisions={"AAPL": "BUY", "MSFT": "SELL"})})

        [result] = engine.run_due()

        buy, sell = store.decisions
        assert (buy.trade.action, buy.trade.size_pct, buy.trade.price) == ("BUY", 0.25, 95.0)
        assert sell.trade.action == "SELL"
        assert result.trades_executed == 2

    def test_hold_non_chiede_un_trade(self):
        store = FakeStore(due=[ALPHA], watchlists={10: [AAPL]})
        engine, _ = _engine(store, {"model-a": FakeProvider()})

        engine.run_due()

        assert store.decisions[0].trade is None

    def test_nessun_prezzo_trade_saltato_decisione_registrata(self):
        store = FakeStore(due=[ALPHA], watchlists={10: [AAPL]}, prices={"AAPL": []})
        engine, _ = _engine(store, {"model-a": FakeProvider(decisions={"AAPL": "BUY"})})

        [result] = engine.run_due()

        assert store.decisions[0].decision == "BUY" and store.decisions[0].trade is None
        assert result.status == "success"

    def test_prezzo_piu_vecchio_della_soglia_trade_saltato(self, caplog):
        store = FakeStore(
            due=[ALPHA], watchlists={10: [AAPL]}, prices={"AAPL": [PricePoint(ts=NOW - timedelta(days=4), close=95.0)]}
        )
        engine, _ = _engine(store, {"model-a": FakeProvider(decisions={"AAPL": "BUY"})}, max_price_age=timedelta(days=3))

        engine.run_due()

        assert store.decisions[0].trade is None
        assert "trade BUY saltato" in caplog.text

    def test_prezzo_del_weekend_entro_la_soglia_esegue(self):
        store = FakeStore(
            due=[ALPHA], watchlists={10: [AAPL]}, prices={"AAPL": [PricePoint(ts=NOW - timedelta(days=2, hours=12), close=95.0)]}
        )
        engine, _ = _engine(store, {"model-a": FakeProvider(decisions={"AAPL": "BUY"})})

        engine.run_due()

        assert store.decisions[0].trade.price == 95.0


class TestInitializePortfolio:
    def test_watchlist_scelta_dal_provider_e_primo_giro(self):
        store = FakeStore()
        provider = FakeProvider(selection=("MSFT", "AAPL"))
        engine, factory = _engine(store, {"model-a": provider})

        result = engine.initialize_portfolio(10)

        assert store.replaced[10] == [2, 1]
        assert provider.calls == ["MSFT", "AAPL"]  # nell'ordine scelto dal provider
        assert factory.requested == [("fake", "model-a")]
        assert result.status == "success"
        assert store.scheduled[10] == NOW + INTERVAL

    def test_symbol_fuori_universo_scartati(self, caplog):
        store = FakeStore()
        engine, _ = _engine(store, {"model-a": FakeProvider(selection=("AAPL", "NONESISTE"))})

        engine.initialize_portfolio(10)

        assert store.replaced[10] == [1]
        assert "NONESISTE" in caplog.text

    def test_selezione_vuota_svuota_la_watchlist_e_crea_un_run_vuoto(self):
        store = FakeStore()
        engine, _ = _engine(store, {"model-a": FakeProvider(selection=())})

        result = engine.initialize_portfolio(10)

        assert store.replaced[10] == []
        assert (result.status, result.decisions_total) == ("success", 0)

    def test_errore_di_select_watchlist_si_propaga(self):
        class Failing(FakeProvider):
            def select_watchlist(self, context):
                raise DecisionError("quota")

        store = FakeStore()
        engine, _ = _engine(store, {"model-a": Failing()})

        with pytest.raises(DecisionError):
            engine.initialize_portfolio(10)
        assert store.replaced == {} and store.runs == {}

    def test_primo_giro_con_decisioni_fallite_non_schedula(self):
        store = FakeStore()
        engine, _ = _engine(store, {"model-a": FakeProvider(failing={"AAPL", "MSFT"})})

        result = engine.initialize_portfolio(10)

        assert result.status == "failed"
        assert 10 not in store.scheduled
