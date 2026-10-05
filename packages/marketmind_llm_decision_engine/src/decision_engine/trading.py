"""Matematica di un trade BUY/SELL, come funzione pura.

`plan_trade` calcola il nuovo cash e la nuova posizione a partire da quelli
correnti, senza toccare il DB: la scrittura la fa `PortfolioRepository`
(`apply_trade`), nella stessa transazione in cui legge lo stato corrente.
Così le regole si testano da sole e i trigger di snapshot del portfolio
scattano come sempre, su un normale `UPDATE`/`INSERT`/`DELETE`.

La size è quella proposta dall'LLM (`Decision.size_pct`): per un BUY la
frazione del cash disponibile da investire, per un SELL la frazione della
posizione corrente da liquidare. Un SELL senza posizione, o una size che
non sposta nulla, non è un errore: il piano risulta semplicemente senza
cambiamenti. `equity_value` non viene ricalcolato (richiederebbe il
mark-to-market di tutte le posizioni, punto aperto).
"""

from __future__ import annotations

from dataclasses import dataclass

# Sotto questa quantità una posizione si considera azzerata.
MIN_QUANTITY = 1e-9


@dataclass(frozen=True)
class PositionState:
    quantity: float
    avg_price: float


@dataclass(frozen=True)
class TradePlan:
    cash_after: float
    position_after: PositionState | None
    changed: bool


def plan_trade(
    *,
    cash: float,
    position: PositionState | None,
    action: str,
    size_pct: float | None,
    price: float,
) -> TradePlan:
    if action not in ("BUY", "SELL"):
        raise ValueError(f"plan_trade gestisce solo BUY/SELL, non {action!r} (HOLD non è un trade)")
    if price <= 0:
        raise ValueError(f"prezzo di esecuzione non positivo: {price}")
    if size_pct is None or not 0 <= size_pct <= 1:
        raise ValueError(f"size_pct deve essere tra 0 e 1, ricevuto {size_pct!r}")

    unchanged = TradePlan(cash_after=cash, position_after=position, changed=False)

    if action == "BUY":
        amount = cash * size_pct
        if amount <= 0:
            return unchanged
        bought = amount / price
        if position is None:
            new_position = PositionState(quantity=bought, avg_price=price)
        else:
            quantity = position.quantity + bought
            avg_price = (position.quantity * position.avg_price + bought * price) / quantity
            new_position = PositionState(quantity=quantity, avg_price=avg_price)
        return TradePlan(cash_after=cash - amount, position_after=new_position, changed=True)

    if position is None or position.quantity <= 0:
        return unchanged
    sold = position.quantity * size_pct
    if sold <= 0:
        return unchanged
    remaining = position.quantity - sold
    new_position = (
        None if remaining <= MIN_QUANTITY else PositionState(quantity=remaining, avg_price=position.avg_price)
    )
    return TradePlan(cash_after=cash + sold * price, position_after=new_position, changed=True)
