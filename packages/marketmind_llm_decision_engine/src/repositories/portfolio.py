"""Letture e scritture su `portfolio` per il Decision Engine.

Ogni metodo lavora su un solo `portfolio_id` (isolamento tra portfolio),
tranne `due_model_portfolios`, che restituisce l'elenco su cui iterare.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from marketmind_db.database import Transaction
from marketmind_db.models.market_data import Asset
from marketmind_db.models.portfolio import Portfolio, PortfolioPosition, PortfolioWatchlistEntry
from marketmind_llm_decision_engine.decision_engine.trading import PositionState, TradePlan
from marketmind_llm_decision_engine.repositories.market_context import AssetRef


@dataclass(frozen=True)
class PortfolioRef:
    """Quello che serve al motore per far girare un portfolio, come valore."""

    portfolio_id: int
    name: str
    llm_provider: str | None
    model_version: str | None

    @classmethod
    def of(cls, portfolio: Portfolio) -> PortfolioRef:
        return cls(portfolio.portfolio_id, portfolio.name, portfolio.llm_provider, portfolio.model_version)


@dataclass(frozen=True)
class PositionView:
    asset_id: int
    symbol: str
    quantity: float
    avg_price: float


class PortfolioRepository:
    def __init__(self, tx: Transaction) -> None:
        self.tx = tx

    # --- letture ----------------------------------------------------------

    def get(self, portfolio_id: int) -> Portfolio:
        portfolio = self.tx.repository(Portfolio).get_one(portfolio_id=portfolio_id)
        if portfolio is None:
            raise LookupError(f"portfolio {portfolio_id} inesistente")
        return portfolio

    def due_model_portfolios(self, as_of: datetime) -> list[Portfolio]:
        """Portfolio `model` attivi il cui giro è dovuto: `next_decision_at`
        nullo (mai schedulato) o già scaduto. Il benchmark è escluso per
        disegno."""
        return self.tx.repository(Portfolio).select(
            where=[
                Portfolio.portfolio_type == "model",
                Portfolio.is_active.is_(True),
                (Portfolio.next_decision_at.is_(None)) | (Portfolio.next_decision_at <= as_of),
            ],
            order_by=("portfolio_id",),
            limit=None,
        )

    def positions(self, portfolio_id: int) -> list[PositionView]:
        rows = self.tx.repository(PortfolioPosition).select(
            where={"portfolio_id": portfolio_id}, limit=None
        )
        symbols = self._symbols([r.asset_id for r in rows])
        return [PositionView(r.asset_id, symbols[r.asset_id], r.quantity, r.avg_price) for r in rows]

    def position(self, portfolio_id: int, asset_id: int) -> PositionState | None:
        row = self.tx.repository(PortfolioPosition).get_one(portfolio_id=portfolio_id, asset_id=asset_id)
        return None if row is None else PositionState(quantity=row.quantity, avg_price=row.avg_price)

    def watchlist(self, portfolio_id: int) -> list[AssetRef]:
        ids = self.tx.repository(PortfolioWatchlistEntry).values(
            "asset_id", where={"portfolio_id": portfolio_id}, limit=None
        )
        if not ids:
            return []
        assets = self.tx.repository(Asset).select(where={"asset_id": ids}, order_by=("symbol",), limit=None)
        return [AssetRef.of(a) for a in assets]

    # --- scritture --------------------------------------------------------

    def replace_watchlist(self, portfolio_id: int, asset_ids: Sequence[int], *, added_at: datetime) -> None:
        """Replace totale, non un merge: il bootstrap ripensa lo scope da zero.
        Una lista vuota svuota la watchlist."""
        entries = self.tx.repository(PortfolioWatchlistEntry)
        entries.delete(where={"portfolio_id": portfolio_id})
        entries.insert(
            [{"portfolio_id": portfolio_id, "asset_id": a, "added_at": added_at} for a in dict.fromkeys(asset_ids)]
        )

    def schedule_next_decision(self, portfolio_id: int, at: datetime) -> None:
        self.tx.repository(Portfolio).update({"next_decision_at": at}, where={"portfolio_id": portfolio_id})

    def apply_trade(self, portfolio_id: int, asset_id: int, plan: TradePlan) -> None:
        """Scrive un `TradePlan` calcolato da `plan_trade` sullo stato letto
        nella stessa transazione: cash, e la posizione creata, aggiornata o
        chiusa. Un piano senza cambiamenti non scrive nulla."""
        if not plan.changed:
            return
        self.tx.repository(Portfolio).update({"cash": plan.cash_after}, where={"portfolio_id": portfolio_id})
        positions = self.tx.repository(PortfolioPosition)
        key = {"portfolio_id": portfolio_id, "asset_id": asset_id}
        if plan.position_after is None:
            positions.delete(where=key)
            return
        positions.upsert(
            [
                key
                | {
                    "quantity": plan.position_after.quantity,
                    "avg_price": plan.position_after.avg_price,
                    "updated_at": datetime.now(timezone.utc),
                }
            ],
            conflict_on=("portfolio_id", "asset_id"),
        )

    def _symbols(self, asset_ids: list[int]) -> dict[int, str]:
        if not asset_ids:
            return {}
        assets = self.tx.repository(Asset).select(where={"asset_id": asset_ids}, limit=None)
        return {a.asset_id: a.symbol for a in assets}
