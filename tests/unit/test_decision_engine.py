"""Test unitari di `DecisionEngine` (`decision_engine/engine.py`).

Nessun DB, nessuna patch globale: il motore riceve uno store in memoria
(`FakeStore`, che implementa `DecisionStore`) e una factory di provider
finti.

Il modello dei cicli: un ciclo è un solo run. All'apertura (portfolio
dovuto) si crea il run, si fissa subito il prossimo ciclo a +intervallo e
si decide su tutta la watchlist; a ogni scatto successivo, dentro il ciclo,
si decide solo sugli asset della watchlist senza decisione in quel run,
aggiungendo le decisioni allo stesso run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
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
from marketmind_llm_decision_engine.decision_engine.store import RunRef
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

ALPHA = PortfolioRef(10, "alpha", "fake", "model-a", next_decision_at=None)
BETA = PortfolioRef(20, "beta", "fake", "model-b", next_decision_at=None)


@dataclass
class RecordedDecision:
    run_id: int
    portfolio_id: int
    symbol: str
    decision: str
    trade: object
    context: dict
    as_of: datetime


@dataclass
class FakeStore:
    portfolios: list[PortfolioRef] = field(default_factory=list)
    watchlists: dict[int, list[AssetRef]] = field(default_factory=dict)
    prices: dict[str, list[PricePoint]] = field(default_factory=dict)
    windows: ContextWindows = ContextWindows()
    runs: dict[int, dict] = field(default_factory=dict)
    decisions: list[RecordedDecision] = field(default_factory=list)
    scheduled: dict[int, datetime] = field(default_factory=dict)
    replaced: dict[int, list[int]] = field(default_factory=dict)
    fail_watchlist_for: set[int] = field(default_factory=set)

    def active_model_portfolios(self):
        return [replace(p, next_decision_at=self.scheduled.get(p.portfolio_id, p.next_decision_at)) for p in self.portfolios]

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
        self.runs[run_id] = {"portfolio_id": portfolio.portfolio_id, "ts": as_of, "status": "running", "error": None, "finishes": 0}
        return run_id

    def latest_run(self, portfolio_id):
        mine = [(rid, r) for rid, r in self.runs.items() if r["portfolio_id"] == portfolio_id]
        if not mine:
            return None
        run_id, run = max(mine, key=lambda x: (x[1]["ts"], x[0]))
        return RunRef(run_id=run_id, ts=run["ts"])

    def decided_asset_ids(self, run_id):
        return {a.asset_id for a in UNIVERSE for d in self.decisions if d.run_id == run_id and d.symbol == a.symbol}

    def finish_run(self, run_id, status, error_message=None):
        self.runs[run_id].update(status=status, error=error_message)
        self.runs[run_id]["finishes"] += 1

    def record_decision(self, *, run_id, portfolio_id, asset, as_of, decision, context, trade):
        self.decisions.append(RecordedDecision(run_id, portfolio_id, asset.symbol, decision.decision, trade, context, as_of))
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


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def _engine(store, providers, clock=None, **kwargs):
    factory = Factory(providers)
    engine = DecisionEngine(store, factory, clock=clock or Clock(), interval=INTERVAL, **kwargs)
    return engine, factory


class TestNuovoCiclo:
    def test_niente_da_fare(self, caplog):
        caplog.set_level(logging.INFO)
        store = FakeStore(portfolios=[replace(ALPHA, next_decision_at=NOW + timedelta(days=1))])
        engine, _ = _engine(store, {})

        assert engine.run_due() == []
        assert store.runs == {}
        assert "niente da fare" in caplog.text

    def test_un_run_per_portfolio_con_provider_proprio(self):
        store = FakeStore(portfolios=[ALPHA, BETA], watchlists={10: [AAPL, MSFT], 20: [NVDA]})
        alpha, beta = FakeProvider(), FakeProvider()
        engine, factory = _engine(store, {"model-a": alpha, "model-b": beta})

        results = engine.run_due()

        assert factory.requested == [("fake", "model-a"), ("fake", "model-b")]
        assert alpha.calls == ["AAPL", "MSFT"] and beta.calls == ["NVDA"]
        assert [(d.run_id, d.symbol) for d in store.decisions] == [(1, "AAPL"), (1, "MSFT"), (2, "NVDA")]
        assert [(r.status, r.kind) for r in results] == [("success", "ciclo"), ("success", "ciclo")]
        assert store.decisions[0].context["portfolio"]["portfolio_id"] == 10

    def test_prossimo_ciclo_fissato_allapertura_anche_se_il_giro_fallisce(self):
        store = FakeStore(portfolios=[ALPHA], watchlists={10: [AAPL, MSFT]})
        engine, _ = _engine(store, {"model-a": FakeProvider(failing={"AAPL", "MSFT"})})

        [result] = engine.run_due()

        assert result.status == "failed"
        assert store.scheduled[10] == NOW + INTERVAL
        assert store.runs[1]["status"] == "failed"
        assert "2/2 asset senza decisione" in store.runs[1]["error"] and "500 INTERNAL" in store.runs[1]["error"]

    def test_esito_partial_sulla_watchlist(self):
        store = FakeStore(portfolios=[ALPHA], watchlists={10: [AAPL, MSFT, NVDA]})
        engine, _ = _engine(store, {"model-a": FakeProvider(failing={"MSFT"})})

        [result] = engine.run_due()

        assert result.status == "partial"
        assert "1/3 asset senza decisione" in store.runs[1]["error"]

    def test_watchlist_vuota(self):
        store = FakeStore(portfolios=[ALPHA], watchlists={10: []})
        engine, _ = _engine(store, {"model-a": FakeProvider()})

        [result] = engine.run_due()

        assert (result.status, result.decisions_total) == ("success", 0)
        assert store.scheduled[10] == NOW + INTERVAL

    def test_next_decision_at_scaduto_apre_un_ciclo_nuovo(self):
        store = FakeStore(portfolios=[replace(ALPHA, next_decision_at=NOW - timedelta(minutes=1))], watchlists={10: [AAPL]})
        engine, _ = _engine(store, {"model-a": FakeProvider()})

        [result] = engine.run_due()

        assert result.kind == "ciclo" and len(store.runs) == 1


class TestRipresa:
    def test_ritenta_solo_gli_asset_senza_decisione_nello_stesso_run(self):
        """Il caso del 10 ottobre: prima il retry rifaceva tutta la watchlist,
        con decisioni e trade ripetuti (AAPL comprato e poi venduto)."""
        clock = Clock()
        store = FakeStore(portfolios=[ALPHA], watchlists={10: [AAPL, MSFT, NVDA]})
        provider = FakeProvider(decisions={"AAPL": "BUY"}, failing={"MSFT", "NVDA"})
        engine, _ = _engine(store, {"model-a": provider}, clock=clock)
        engine.run_due()

        clock.now = NOW + timedelta(hours=1)
        provider.failing = {"NVDA"}
        provider.calls.clear()
        [retry] = engine.run_due()

        assert provider.calls == ["MSFT", "NVDA"]
        assert (retry.kind, retry.run_id) == ("ripresa", 1)
        assert [d.symbol for d in store.decisions] == ["AAPL", "MSFT"]
        assert [d.trade is not None for d in store.decisions if d.symbol == "AAPL"] == [True]
        assert store.decisions[1].as_of == NOW + timedelta(hours=1)
        assert retry.status == "partial" and store.runs[1]["status"] == "partial"
        assert store.scheduled[10] == NOW + INTERVAL
        assert len(store.runs) == 1

    def test_ciclo_completato_diventa_success_e_poi_non_chiama_piu_il_modello(self):
        clock = Clock()
        store = FakeStore(portfolios=[ALPHA], watchlists={10: [AAPL, MSFT]})
        provider = FakeProvider(failing={"MSFT"})
        engine, _ = _engine(store, {"model-a": provider}, clock=clock)
        engine.run_due()

        clock.now = NOW + timedelta(hours=1)
        provider.failing = set()
        engine.run_due()
        clock.now = NOW + timedelta(hours=2)
        provider.calls.clear()
        later = engine.run_due()

        assert store.runs[1]["status"] == "success" and store.runs[1]["error"] is None
        assert later == [] and provider.calls == []

    def test_il_ciclo_successivo_riparte_da_tutta_la_watchlist(self):
        clock = Clock()
        store = FakeStore(portfolios=[ALPHA], watchlists={10: [AAPL, MSFT]})
        provider = FakeProvider(failing={"MSFT"})
        engine, _ = _engine(store, {"model-a": provider}, clock=clock)
        engine.run_due()

        clock.now = NOW + INTERVAL
        provider.failing = set()
        provider.calls.clear()
        [result] = engine.run_due()

        assert result.kind == "ciclo" and result.run_id == 2
        assert provider.calls == ["AAPL", "MSFT"]
        assert store.scheduled[10] == NOW + 2 * INTERVAL

    def test_portfolio_mai_girato_e_non_dovuto_non_fa_nulla(self):
        store = FakeStore(portfolios=[replace(ALPHA, next_decision_at=NOW + timedelta(days=2))], watchlists={10: [AAPL]})
        engine, _ = _engine(store, {"model-a": FakeProvider()})

        assert engine.run_due() == []


class TestIsolamento:
    def test_errore_su_un_portfolio_non_blocca_gli_altri(self):
        store = FakeStore(portfolios=[ALPHA, BETA], watchlists={10: [AAPL], 20: [NVDA]}, fail_watchlist_for={10})
        engine, _ = _engine(store, {"model-a": FakeProvider(), "model-b": FakeProvider()})

        results = engine.run_due()

        assert [(r.portfolio_id, r.status) for r in results] == [(10, "failed"), (20, "success")]
        assert [d.symbol for d in store.decisions] == ["NVDA"]

    def test_provider_non_costruibile_e_failed_senza_run(self):
        store = FakeStore(portfolios=[ALPHA, BETA], watchlists={10: [AAPL], 20: [NVDA]})
        engine, _ = _engine(store, {"model-a": ValueError("provider sconosciuto"), "model-b": FakeProvider()})

        results = engine.run_due()

        assert results[0].status == "failed" and results[0].run_id is None
        assert results[1].status == "success"

    def test_errore_dello_store_durante_il_giro_chiude_il_run_failed(self):
        class BrokenStore(FakeStore):
            def record_decision(self, **kwargs):
                raise RuntimeError("scrittura fallita")

        store = BrokenStore(portfolios=[ALPHA], watchlists={10: [AAPL]})
        engine, _ = _engine(store, {"model-a": FakeProvider()})

        [result] = engine.run_due()

        assert result.status == "failed"
        assert store.runs[1]["status"] == "failed"

    def test_interruzione_chiude_il_run_failed_e_si_propaga(self):
        class Stop(BaseException):
            pass

        class Interrupting(FakeProvider):
            def decide(self, context):
                raise Stop()

        store = FakeStore(portfolios=[ALPHA], watchlists={10: [AAPL, MSFT]})
        engine, _ = _engine(store, {"model-a": Interrupting()})

        with pytest.raises(Stop):
            engine.run_due()
        assert store.runs[1]["status"] == "failed"
        assert "interrotto" in store.runs[1]["error"]
        # Il prossimo ciclo era già fissato all'apertura: lo scatto successivo
        # riprende il run invece di aprirne un secondo.
        assert store.scheduled[10] == NOW + INTERVAL


class TestTrade:
    def test_buy_e_sell_chiedono_un_trade_allultimo_prezzo(self):
        store = FakeStore(
            portfolios=[ALPHA],
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
        store = FakeStore(portfolios=[ALPHA], watchlists={10: [AAPL]})
        engine, _ = _engine(store, {"model-a": FakeProvider()})

        engine.run_due()

        assert store.decisions[0].trade is None

    def test_nessun_prezzo_trade_saltato_decisione_registrata(self):
        store = FakeStore(portfolios=[ALPHA], watchlists={10: [AAPL]}, prices={"AAPL": []})
        engine, _ = _engine(store, {"model-a": FakeProvider(decisions={"AAPL": "BUY"})})

        [result] = engine.run_due()

        assert store.decisions[0].decision == "BUY" and store.decisions[0].trade is None
        assert result.status == "success"

    def test_prezzo_piu_vecchio_della_soglia_trade_saltato(self, caplog):
        store = FakeStore(
            portfolios=[ALPHA], watchlists={10: [AAPL]}, prices={"AAPL": [PricePoint(ts=NOW - timedelta(days=4), close=95.0)]}
        )
        engine, _ = _engine(store, {"model-a": FakeProvider(decisions={"AAPL": "BUY"})}, max_price_age=timedelta(days=3))

        engine.run_due()

        assert store.decisions[0].trade is None
        assert "trade BUY saltato" in caplog.text

    def test_prezzo_del_weekend_entro_la_soglia_esegue(self):
        store = FakeStore(
            portfolios=[ALPHA], watchlists={10: [AAPL]}, prices={"AAPL": [PricePoint(ts=NOW - timedelta(days=2, hours=12), close=95.0)]}
        )
        engine, _ = _engine(store, {"model-a": FakeProvider(decisions={"AAPL": "BUY"})})

        engine.run_due()

        assert store.decisions[0].trade.price == 95.0


class TestInitializePortfolio:
    def test_watchlist_scelta_dal_provider_e_primo_ciclo(self):
        store = FakeStore()
        provider = FakeProvider(selection=("MSFT", "AAPL"))
        engine, factory = _engine(store, {"model-a": provider})

        result = engine.initialize_portfolio(10)

        assert store.replaced[10] == [2, 1]
        assert provider.calls == ["MSFT", "AAPL"]  # nell'ordine scelto dal provider
        assert factory.requested == [("fake", "model-a")]
        assert (result.status, result.kind) == ("success", "ciclo")
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

    def test_primo_ciclo_con_decisioni_fallite_viene_ripreso(self):
        clock = Clock()
        store = FakeStore(portfolios=[ALPHA])
        provider = FakeProvider(failing={"AAPL"})
        engine, _ = _engine(store, {"model-a": provider}, clock=clock)

        first = engine.initialize_portfolio(10)
        clock.now = NOW + timedelta(hours=1)
        provider.failing = set()
        provider.calls.clear()
        [retry] = engine.run_due()

        assert first.status == "partial"
        assert provider.calls == ["AAPL"] and retry.run_id == first.run_id
