"""Dashboard Streamlit — interfaccia di visualizzazione, sola lettura.

Unico entry point standalone del modulo (avviato con `streamlit run
src/marketmind_ai/dashboard/app.py`, non un modulo di libreria): chiama
`configure_logging()` all'avvio, coerente con la convenzione di logging di
progetto (`Market Mind AI - Docs/Architettura/04_logging.md`). Non fa
query dirette a Postgres: passa sempre da `db/dashboard_reader.py` (più
`db/portfolio_reader.py` per lo stato corrente di un portfolio), lo stesso
principio già seguito da `decision_engine/`/`backtest/` — "solo `db/`
parla con Postgres" (`Market Mind AI - Docs/Architettura/
00_struttura_cartelle.md`).

Scope v1 (`Market Mind AI - Docs/Dashboard/00_dashboard.md`): stato
corrente di un portfolio selezionato (cash, posizioni, watchlist), cash nel
tempo, log delle decisioni con reasoning. Niente Sharpe/drawdown di periodo
(richiederebbe più run di backtest reali di quanti ne esistano oggi) né
confronto col benchmark — entrambi rimandati a un'iterazione successiva,
non sovradimensionare la dashboard prima di avere dati reali da mostrare
(`Market Mind AI.md` §2.7).

Le funzioni `_*` sotto sono pure (nessun `st.*`/sessione al loro interno):
costruiscono le strutture dati che il resto dello script renderizza, così
da restare testabili senza il runtime di Streamlit — stesso principio già
seguito da `decision_engine/context_builder.py` rispetto a `engine.py`.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from marketmind_ai.db.dashboard_reader import get_cash_history, get_decision_log, list_portfolios
from marketmind_ai.db.models.decisions import ModelDecision
from marketmind_ai.db.models.portfolio import Portfolio, PortfolioPosition, PortfolioSnapshot
from marketmind_ai.db.portfolio_reader import get_portfolio_positions, get_watchlist
from marketmind_ai.db.session import get_session
from marketmind_ai.utils.logging_config import configure_logging

_CASH_LINE_COLOR = "#3B82F6"  # blu neutro, unica serie: nessuna scelta categoriale da validare


def _portfolio_label(portfolio: Portfolio) -> str:
    return f"{portfolio.name} ({portfolio.portfolio_type})"


def _cost_basis(positions: list[PortfolioPosition]) -> float:
    """Costo storico delle posizioni aperte — non il valore di mercato
    corrente: nessun prezzo live è letto qui, coerente con l'assenza di
    mark-to-market nel resto del progetto."""
    return sum(p.quantity * p.avg_price for p in positions)


def _positions_dataframe(positions: list[PortfolioPosition]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Symbol": p.asset.symbol,
                "Quantità": p.quantity,
                "Prezzo medio": p.avg_price,
                "Costo": p.quantity * p.avg_price,
            }
            for p in positions
        ]
    )


def _decisions_dataframe(decisions: list[ModelDecision]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Data": d.ts,
                "Symbol": d.asset.symbol,
                "Decisione": d.decision,
                "Confidence": d.confidence,
                "Size %": d.size_pct,
                "Reasoning": d.reasoning,
            }
            for d in decisions
        ]
    )


def _cash_history_dataframe(history: list[PortfolioSnapshot]) -> pd.DataFrame:
    return pd.DataFrame({"data": [h.changed_at for h in history], "cash": [h.cash for h in history]})


def _cash_chart(history: list[PortfolioSnapshot]) -> go.Figure:
    df = _cash_history_dataframe(history)
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["data"],
            y=df["cash"],
            mode="lines",
            line=dict(width=2, color=_CASH_LINE_COLOR),
        )
    )
    fig.update_layout(
        margin=dict(l=0, r=0, t=10, b=0),
        height=320,
        xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(128,128,128,0.15)", tickprefix="$"),
        showlegend=False,
    )
    return fig


def _render(portfolios: list[Portfolio]) -> None:
    if not portfolios:
        st.warning("Nessun portfolio trovato.")
        return

    labels = {p.portfolio_id: _portfolio_label(p) for p in portfolios}
    selected_id = st.sidebar.selectbox(
        "Portfolio", options=list(labels), format_func=lambda pid: labels[pid]
    )
    selected = next(p for p in portfolios if p.portfolio_id == selected_id)

    with get_session() as session:
        positions = get_portfolio_positions(session, selected_id)
        watchlist = (
            get_watchlist(session, selected_id) if selected.portfolio_type == "model" else []
        )
        cash_history = get_cash_history(session, selected_id)
        decisions = get_decision_log(session, selected_id)

    st.header(selected.name)
    status = "attivo" if selected.is_active else "sospeso"
    st.caption(f"{selected.portfolio_type} · {status}")

    if selected.portfolio_type == "model":
        st.markdown(f"**Provider:** {selected.llm_provider} ({selected.model_version})")
        with st.expander("Strategia"):
            st.write(selected.strategy_prompt)

    col1, col2, col3 = st.columns(3)
    col1.metric("Cash", f"${selected.cash:,.2f}")
    col2.metric("Capitale iniziale", f"${selected.starting_capital:,.2f}")
    col3.metric("Costo posizioni aperte", f"${_cost_basis(positions):,.2f}")

    st.subheader("Cash nel tempo")
    st.caption(
        "Non una vera equity curve: il valore di mercato delle posizioni aperte "
        "non è ancora ricalcolato a ogni trade (mark-to-market non implementato, "
        "lasciato esplicitamente aperto)."
    )
    if len(cash_history) >= 2:
        st.plotly_chart(_cash_chart(cash_history), use_container_width=True)
    elif cash_history:
        # Un solo punto non fa una curva: un grafico a un punto solo
        # produce un asse temporale privo di senso (Plotly deduce un range
        # dai microsecondi), meglio mostrare il valore direttamente.
        st.info(f"Un solo valore finora: ${cash_history[0].cash:,.2f}.")
    else:
        st.info("Nessuno storico ancora disponibile.")

    st.subheader("Posizioni aperte")
    if positions:
        st.dataframe(_positions_dataframe(positions), hide_index=True, use_container_width=True)
    else:
        st.info("Nessuna posizione aperta.")

    if selected.portfolio_type == "model":
        st.subheader("Watchlist")
        if watchlist:
            st.write(", ".join(sorted(a.symbol for a in watchlist)))
        else:
            st.info("Watchlist vuota — portfolio non ancora inizializzato.")

    st.subheader("Log decisioni")
    if decisions:
        st.dataframe(_decisions_dataframe(decisions), hide_index=True, use_container_width=True)
    else:
        st.info("Nessuna decisione ancora registrata.")


def main() -> None:
    configure_logging()
    st.set_page_config(page_title="AI Market Mind", layout="wide")
    st.title("AI Market Mind")

    with get_session() as session:
        portfolios = list_portfolios(session)

    _render(portfolios)


if __name__ == "__main__":
    main()
