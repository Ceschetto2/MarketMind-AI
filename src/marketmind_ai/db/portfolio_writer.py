"""Scrittura verso `portfolio` — watchlist e trade reali.

File a sé rispetto a `portfolio_reader.py` (letture) e a `decision_writer.py`
(scritture verso lo schema `decisions`). `execute_trade()` è il punto in cui
una `Decision` BUY/SELL diventa capitale spostato per davvero: aggiorna
`t_portfolios.cash` e crea/aggiorna/cancella la riga corrispondente in
`t_portfolio_positions`. Non è compito del Backtesting Engine (non ancora
scritto) — decision_engine/ esegue le proprie decisioni in avanti, mano a
mano che le prende; il Backtesting Engine resta un motore di analisi
retrospettiva (Sharpe, drawdown, PnL su un periodo, via vectorbt), non il
proprietario dell'esecuzione.

Nessun ricalcolo di `equity_value` qui: richiederebbe il prezzo corrente di
*tutte* le posizioni del portfolio, non solo di quella appena scambiata —
mark-to-market completo, non ancora implementato, lasciato esplicitamente
aperto.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from marketmind_ai.db.models.portfolio import Portfolio, PortfolioPosition, PortfolioWatchlistEntry
from marketmind_ai.llm.schemas import Decision

_MIN_QUANTITY = 1e-9  # sotto questa soglia, una posizione si considera azzerata


def write_watchlist(
    session: Session, portfolio_id: int, asset_ids: list[int], added_at: datetime
) -> None:
    """Sostituisce la watchlist di un portfolio con l'insieme dato.

    Un replace totale, non un merge incrementale: il bootstrap
    (`decision_engine.engine.initialize_portfolio`) ripensa lo scope da zero
    ogni volta che viene rilanciato, non lo estende. Una watchlist vuota
    (`asset_ids=[]`) è un caso valido — svuota lo scope, non un errore.
    """
    session.execute(
        delete(PortfolioWatchlistEntry).where(
            PortfolioWatchlistEntry.portfolio_id == portfolio_id
        )
    )
    for asset_id in asset_ids:
        session.add(
            PortfolioWatchlistEntry(portfolio_id=portfolio_id, asset_id=asset_id, added_at=added_at)
        )
    session.flush()


def schedule_next_decision(
    session: Session, portfolio_id: int, next_decision_at: datetime
) -> None:
    """Scrive quando il Decision Engine dovrà girare di nuovo per questo
    portfolio (`t_portfolios.next_decision_at`).

    Non un cron fisso lato timer: `next_decision_at` è un valore che il
    motore stesso calcola dopo ogni giro (`decision_engine.engine`,
    default: +7 giorni) e può quindi sovrascrivere con un valore diverso
    dal default per un singolo portfolio, in futuro anche in risposta a un
    rinvio dell'LLM (`Decision.defer`, non ancora collegato qui) — il
    timer condiviso (`get_due_model_portfolios`) si limita a leggerlo."""
    session.execute(
        update(Portfolio)
        .where(Portfolio.portfolio_id == portfolio_id)
        .values(next_decision_at=next_decision_at)
    )
    session.flush()


def execute_trade(
    session: Session, portfolio_id: int, asset_id: int, decision: Decision, price: float
) -> None:
    """Esegue un `BUY`/`SELL` sul portfolio al `price` dato (l'ultimo
    prezzo noto nel context package, mai un dato futuro).

    `decision.size_pct` (già validato da `Decision`, obbligatorio per
    BUY/SELL) è la percentuale di cash disponibile da investire (`BUY`) o
    di posizione corrente da liquidare (`SELL`) — la size del trade è
    quella proposta dall'LLM, non una regola deterministica calcolata qui.
    Un `SELL` senza posizione, o una size che risulta in una quantità
    trascurabile, è un no-op silenzioso: non c'è nulla da vendere/comprare,
    non un errore. Solleva `ValueError` se chiamata su un `HOLD` — non va
    mai chiamata in quel caso, è un errore del chiamante, non un caso
    d'uso legittimo.
    """
    if decision.decision not in ("BUY", "SELL"):
        raise ValueError(
            f"execute_trade chiamata con decision={decision.decision!r}: gestisce solo BUY/SELL"
        )

    portfolio = session.execute(
        select(Portfolio).where(Portfolio.portfolio_id == portfolio_id)
    ).scalar_one()
    position = session.execute(
        select(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == portfolio_id,
            PortfolioPosition.asset_id == asset_id,
        )
    ).scalar_one_or_none()

    if decision.decision == "BUY":
        _execute_buy(session, portfolio, position, asset_id, decision.size_pct, price)
    else:
        _execute_sell(session, portfolio, position, decision.size_pct, price)

    session.flush()


def _execute_buy(
    session: Session,
    portfolio: Portfolio,
    position: PortfolioPosition | None,
    asset_id: int,
    size_pct: float,
    price: float,
) -> None:
    amount = portfolio.cash * size_pct
    if amount <= 0:
        return
    quantity_bought = amount / price

    portfolio.cash -= amount
    if position is None:
        session.add(
            PortfolioPosition(
                portfolio_id=portfolio.portfolio_id,
                asset_id=asset_id,
                quantity=quantity_bought,
                avg_price=price,
                updated_at=datetime.now(timezone.utc),
            )
        )
    else:
        new_quantity = position.quantity + quantity_bought
        position.avg_price = (
            position.quantity * position.avg_price + quantity_bought * price
        ) / new_quantity
        position.quantity = new_quantity
        position.updated_at = datetime.now(timezone.utc)


def _execute_sell(
    session: Session,
    portfolio: Portfolio,
    position: PortfolioPosition | None,
    size_pct: float,
    price: float,
) -> None:
    if position is None or position.quantity <= 0:
        return
    quantity_to_sell = position.quantity * size_pct
    if quantity_to_sell <= 0:
        return

    portfolio.cash += quantity_to_sell * price
    remaining = position.quantity - quantity_to_sell
    if remaining <= _MIN_QUANTITY:
        session.delete(position)
    else:
        position.quantity = remaining
        position.updated_at = datetime.now(timezone.utc)
