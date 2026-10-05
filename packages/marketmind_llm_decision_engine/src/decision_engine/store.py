"""Il confine tra la logica del motore e Postgres.

`DecisionEngine` parla solo con un `DecisionStore`: quali portfolio sono
dovuti, la loro watchlist, il contesto di una decisione, la registrazione di
run, decisioni e trade. `PostgresDecisionStore` lo implementa sopra i
repository, aprendo una transazione per operazione; nei test unitari il
motore usa un'implementazione in memoria.

La registrazione di una decisione e dell'eventuale trade avviene in una
sola transazione, dentro `track_model_run(run_id)`: i trigger di snapshot
del portfolio collegano cash e posizione al run che li ha originati (serve
al Backtesting Engine), e la decisione non resta mai registrata senza il
trade deciso, o viceversa.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from marketmind_db.audit import RunStatus
from marketmind_db.database import Database
from marketmind_db.run_context import track_model_run
from marketmind_llm_decision_engine.decision_engine.context_builder import ContextBuilder, ContextWindows
from marketmind_llm_decision_engine.decision_engine.schemas import BootstrapContext, DecisionContext
from marketmind_llm_decision_engine.decision_engine.trading import plan_trade
from marketmind_llm_decision_engine.llm.schemas import Decision
from marketmind_llm_decision_engine.repositories.decisions import DecisionRepository
from marketmind_llm_decision_engine.repositories.market_context import AssetRef, MarketContextRepository
from marketmind_llm_decision_engine.repositories.portfolio import PortfolioRef, PortfolioRepository


@dataclass(frozen=True)
class TradeRequest:
    """Il trade che il motore chiede di eseguire per una decisione BUY/SELL,
    al prezzo scelto (l'ultimo noto nel contesto, già controllato)."""

    action: str
    size_pct: float
    price: float


class DecisionStore(Protocol):
    windows: ContextWindows

    def due_portfolios(self, as_of: datetime) -> list[PortfolioRef]: ...

    def portfolio(self, portfolio_id: int) -> PortfolioRef: ...

    def watchlist(self, portfolio_id: int) -> list[AssetRef]: ...

    def decision_universe(self) -> list[AssetRef]: ...

    def decision_context(self, asset: AssetRef, *, portfolio_id: int, as_of: datetime) -> DecisionContext: ...

    def bootstrap_context(self, portfolio_id: int) -> BootstrapContext: ...

    def replace_watchlist(self, portfolio_id: int, asset_ids: Sequence[int], *, added_at: datetime) -> None: ...

    def start_run(self, portfolio: PortfolioRef, *, as_of: datetime) -> int: ...

    def finish_run(self, run_id: int, status: RunStatus, error_message: str | None = None) -> None: ...

    def record_decision(
        self,
        *,
        run_id: int,
        portfolio_id: int,
        asset: AssetRef,
        as_of: datetime,
        decision: Decision,
        context: dict[str, Any],
        trade: TradeRequest | None,
    ) -> bool: ...

    def schedule_next_decision(self, portfolio_id: int, at: datetime) -> None: ...


class PostgresDecisionStore:
    def __init__(self, db: Database, windows: ContextWindows = ContextWindows()) -> None:
        self.db = db
        self.windows = windows

    def due_portfolios(self, as_of: datetime) -> list[PortfolioRef]:
        with self.db.transaction() as tx:
            return [PortfolioRef.of(p) for p in PortfolioRepository(tx).due_model_portfolios(as_of)]

    def portfolio(self, portfolio_id: int) -> PortfolioRef:
        with self.db.transaction() as tx:
            return PortfolioRef.of(PortfolioRepository(tx).get(portfolio_id))

    def watchlist(self, portfolio_id: int) -> list[AssetRef]:
        with self.db.transaction() as tx:
            return PortfolioRepository(tx).watchlist(portfolio_id)

    def decision_universe(self) -> list[AssetRef]:
        with self.db.transaction() as tx:
            return MarketContextRepository(tx).decision_universe()

    def decision_context(self, asset: AssetRef, *, portfolio_id: int, as_of: datetime) -> DecisionContext:
        with self.db.transaction() as tx:
            return ContextBuilder(tx, self.windows).build(asset, portfolio_id=portfolio_id, as_of=as_of)

    def bootstrap_context(self, portfolio_id: int) -> BootstrapContext:
        with self.db.transaction() as tx:
            return ContextBuilder(tx, self.windows).build_bootstrap(portfolio_id)

    def replace_watchlist(self, portfolio_id: int, asset_ids: Sequence[int], *, added_at: datetime) -> None:
        with self.db.transaction() as tx:
            PortfolioRepository(tx).replace_watchlist(portfolio_id, asset_ids, added_at=added_at)

    def start_run(self, portfolio: PortfolioRef, *, as_of: datetime) -> int:
        with self.db.transaction() as tx:
            return DecisionRepository(tx).create_run(
                portfolio_id=portfolio.portfolio_id,
                ts=as_of,
                config=self.windows.as_config(),
                llm_provider=portfolio.llm_provider or "",
                model_version=portfolio.model_version or "",
            )

    def finish_run(self, run_id: int, status: RunStatus, error_message: str | None = None) -> None:
        with self.db.transaction() as tx:
            DecisionRepository(tx).finish_run(run_id, status, error_message)

    def record_decision(
        self,
        *,
        run_id: int,
        portfolio_id: int,
        asset: AssetRef,
        as_of: datetime,
        decision: Decision,
        context: dict[str, Any],
        trade: TradeRequest | None,
    ) -> bool:
        """Registra la decisione e, se richiesto, esegue il trade sullo stato
        letto nella stessa transazione. Restituisce se il trade ha spostato
        cash o posizione."""
        with track_model_run(run_id), self.db.transaction() as tx:
            DecisionRepository(tx).write_decision(
                run_id=run_id, asset_id=asset.asset_id, ts=as_of, decision=decision, context_snapshot=context
            )
            if trade is None:
                return False
            portfolios = PortfolioRepository(tx)
            plan = plan_trade(
                cash=portfolios.get(portfolio_id).cash,
                position=portfolios.position(portfolio_id, asset.asset_id),
                action=trade.action,
                size_pct=trade.size_pct,
                price=trade.price,
            )
            portfolios.apply_trade(portfolio_id, asset.asset_id, plan)
            return plan.changed

    def schedule_next_decision(self, portfolio_id: int, at: datetime) -> None:
        with self.db.transaction() as tx:
            PortfolioRepository(tx).schedule_next_decision(portfolio_id, at)
