"""Query verso `portfolio`/`decisions` per `dashboard/` (Streamlit).

Coerente col principio "solo `db/` parla con Postgres"
(`Market Mind AI - Docs/Architettura/00_struttura_cartelle.md`): la
dashboard non costruisce SQLAlchemy proprio, chiama queste funzioni. File a
sé rispetto a `portfolio_reader.py` (scoped a `decision_engine/`, un
`portfolio_id` alla volta, mai la lista di tutti i portfolio) e a
`backtest_reader.py` (scoped a un run specifico): la dashboard ha bisogno
di un elenco per popolare il selettore, e di storico esteso (cash nel
tempo, log decisioni) che nessuno degli altri due consumatori legge oggi.

`get_cash_history()` legge `t_portfolio_snapshots`, non `t_portfolio_
positions`/`t_portfolio_position_snapshots`: `equity_value` non è ancora
ricalcolato a ogni trade (mark-to-market non implementato, lasciato
esplicitamente aperto — `CLAUDE.md` § Ambiguità aperte), quindi resta
fermo al capitale iniziale; solo `cash` riflette davvero i trade eseguiti
nel tempo. La dashboard mostra questa curva onestamente come "cash nel
tempo", non come una vera equity curve mark-to-market.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from marketmind_ai.db.models.decisions import ModelDecision, ModelRun
from marketmind_ai.db.models.portfolio import Portfolio, PortfolioSnapshot


def list_portfolios(session: Session) -> list[Portfolio]:
    """Tutti i portfolio, `model` e `benchmark`, attivi o sospesi — a
    differenza di `portfolio_reader.get_active_model_portfolios()`, che
    resta scoped a ciò su cui deve girare il Decision Engine. La dashboard
    deve poter mostrare anche un portfolio sospeso o il benchmark."""
    return list(session.execute(select(Portfolio).order_by(Portfolio.name)).scalars())


def get_cash_history(session: Session, portfolio_id: int) -> list[PortfolioSnapshot]:
    """Storico di `cash` nel tempo per un portfolio, in ordine cronologico
    — l'intera serie, nessuna finestra: alla scala odierna (pochi run) non
    giustifica un windowing, da rivedere se il volume di snapshot dovesse
    crescere molto."""
    return list(
        session.execute(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.portfolio_id == portfolio_id)
            .order_by(PortfolioSnapshot.changed_at)
        ).scalars()
    )


def get_decision_log(
    session: Session, portfolio_id: int, limit: int = 50
) -> list[ModelDecision]:
    """Le decisioni più recenti di un portfolio (BUY/SELL/HOLD, con
    reasoning), con `.asset` già caricato (`joinedload`) — non l'universo
    intero: solo le decisioni di run appartenenti a questo portfolio
    (`t_model_runs.portfolio_id`, join implicito)."""
    return list(
        session.execute(
            select(ModelDecision)
            .join(ModelRun, ModelRun.run_id == ModelDecision.run_id)
            .options(joinedload(ModelDecision.asset))
            .where(ModelRun.portfolio_id == portfolio_id)
            .order_by(ModelDecision.ts.desc())
            .limit(limit)
        ).scalars()
    )
