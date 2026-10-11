"""Dashboard Streamlit di MarketMind AI.

Unico entry point standalone del pacchetto (`streamlit run
packages/marketmind_frontend/src/app.py`): chiama `configure_logging()`
all'avvio, come ogni entry point di progetto
(`Market Mind AI - Docs/Architettura/04_logging.md`). Tre pagine:

- **Portfolio**: equity mark-to-market ricostruita in sola lettura
  (`analytics`), rendimento contro il benchmark (SPY comprato e tenuto),
  posizioni valorizzate all'ultimo close, log delle decisioni, cicli del
  Decision Engine con esito ed errore, risultati di backtest.
- **Gestione**: creazione di un portfolio `model`, sospensione, modello e
  strategia, nuovo ciclo forzato, bootstrap (`init-portfolio`).
- **Sistema**: ultimo esito di ogni pipeline di ingestion, avvio manuale di
  pipeline e di un giro del Decision Engine, stato delle unit.

Le letture passano da `repository` col ruolo `marketmind_readonly`, le
scritture di gestione da `admin` con `marketmind_app` (`connections`); i
job partono come unit systemd (`jobs`), mai dentro questo processo. Le
funzioni `_*` che costruiscono tabelle e grafici sono pure (nessun `st.*`),
testabili senza il runtime di Streamlit; le funzioni di pagina no, stesso
limite già accettato per gli entry point delle pipeline.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from marketmind_common.logging_config import configure_logging
from marketmind_frontend.admin import LLM_PROVIDERS, NewModelPortfolio, PortfolioAdmin
from marketmind_frontend.analytics import (
    PositionValuation,
    benchmark_comparison,
    equity_curve,
    performance_summary,
    valuation,
)
from marketmind_frontend.connections import DashboardDatabases, dashboard_databases
from marketmind_frontend.jobs import DECIDE_UNIT, INIT_PORTFOLIO_UNIT, JobLaunchError, SystemdJobLauncher
from marketmind_frontend.repository import DashboardRepository
from marketmind_frontend.views import (
    BacktestView,
    CashPoint,
    DecisionView,
    IngestionRunView,
    PortfolioView,
    PositionChange,
    PositionView,
    PricePoint,
    RunView,
)

DISPLAY_TZ = ZoneInfo("Europe/Rome")
# Slot 1 e 2 della palette categorica di riferimento, validati come coppia
# (CVD e visione normale) su superficie chiara e scura.
_SERIES_1 = "#2a78d6"
_SERIES_2 = "#eb6834"
# Prezzi precedenti alla creazione del portfolio letti per dare un valore
# ai primi punti della curva (ultimo close noto, anche di un venerdì).
_PRICE_LOOKBACK = timedelta(days=7)
# Gli errori dei provider LLM includono l'intero payload della risposta:
# nel riquadro d'avviso ne basta l'inizio, il testo completo è a richiesta.
_NOTICE_CHARS = 300
_STATUS_ICONS = {"success": "✅", "partial": "⚠️", "failed": "❌", "running": "⏳"}


# --- formattazione (pura) ---------------------------------------------------------


def _local(ts: datetime | None) -> datetime | None:
    return ts.astimezone(DISPLAY_TZ) if ts is not None else None


def _money(value: float | None) -> str:
    return "—" if value is None else f"${value:,.2f}"


def _pct(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{value:+.2%}"


def _age(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 48 * 60:
        return f"{minutes // 60} h"
    return f"{minutes // (24 * 60)} g"


def _truncate(text: str, limit: int | None = None) -> str:
    limit = _NOTICE_CHARS if limit is None else limit
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _status_label(status: str | None) -> str:
    if status is None:
        return "n/d"
    return f"{_STATUS_ICONS.get(status, '')} {status}".strip()


# --- tabelle (pure) ---------------------------------------------------------------


def _valuation_dataframe(rows: Sequence[PositionValuation]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Symbol": r.symbol,
                "Quantità": r.quantity,
                "Prezzo medio": r.avg_price,
                "Ultimo prezzo": r.last_price,
                "Data prezzo": _local(r.price_ts),
                "Costo": r.cost,
                "Valore": r.market_value,
                "P&L": r.pnl,
                "P&L %": r.pnl_pct,
            }
            for r in rows
        ]
    )


def _decisions_dataframe(decisions: Sequence[DecisionView]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Data": _local(d.ts),
                "Run": d.run_id,
                "Symbol": d.symbol,
                "Decisione": d.decision,
                "Confidence": d.confidence,
                "Size %": d.size_pct,
                "Reasoning": d.reasoning,
                "Ricontrollo": _local(d.recheck_at),
                "Motivo rinvio": d.recheck_reason,
            }
            for d in decisions
        ]
    )


def _runs_dataframe(runs: Sequence[RunView]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Run": r.run_id,
                "Inizio ciclo": _local(r.ts),
                "Esito": _status_label(r.status),
                "Ultimo tentativo": _local(r.finished_at),
                "Decisioni": r.decisions,
                "Modello": r.model_version,
                "Errore": r.error_message,
            }
            for r in runs
        ]
    )


def _backtests_dataframe(results: Sequence[BacktestView]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Calcolato": _local(b.created_at),
                "Periodo": f"{b.period_start} → {b.period_end}",
                "PnL": b.pnl,
                "Rendimento": b.total_return,
                "SPY stesso periodo": b.benchmark_return,
                "Sharpe": b.sharpe_ratio,
                "Max drawdown": b.max_drawdown,
                "Win rate": b.win_rate,
                "Run (vecchio POC)": b.run_id,
            }
            for b in results
        ]
    )


def _ingestion_dataframe(runs: Sequence[IngestionRunView], now: datetime) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Fonte": r.source,
                "Tabella": r.target_table,
                "Esito": _status_label(r.status),
                "Ultimo avvio": _local(r.started_at),
                "Età": _age(now - r.started_at),
                "Durata": _age(r.finished_at - r.started_at) if r.finished_at else None,
                "Righe": r.rows_written,
                "Errore": r.error_message,
            }
            for r in runs
        ]
    )


# --- grafici (puri) ---------------------------------------------------------------


# Linee a gradini (`hv`): ogni valore resta valido fino al punto successivo,
# come nel calcolo (ultimo cash/close noto), non un'interpolazione.
def _line_layout(fig: go.Figure, *, tickformat: str | None = None, tickprefix: str | None = None) -> go.Figure:
    fig.update_layout(
        margin=dict(l=0, r=0, t=10, b=0),
        height=340,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(128,128,128,0.15)", tickformat=tickformat, tickprefix=tickprefix),
    )
    return fig


def _equity_chart(curve: pd.DataFrame) -> go.Figure:
    index = [_local(ts) for ts in curve.index]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=index, y=curve["equity"], name="Equity", mode="lines",
                             line=dict(width=2, color=_SERIES_1, shape="hv"), hovertemplate="$%{y:,.2f}"))
    fig.add_trace(go.Scatter(x=index, y=curve["cash"], name="di cui cash", mode="lines",
                             line=dict(width=2, color=_SERIES_2, shape="hv"), hovertemplate="$%{y:,.2f}"))
    return _line_layout(fig, tickprefix="$")


def _comparison_chart(comparison: pd.DataFrame, benchmark_symbol: str | None) -> go.Figure:
    index = [_local(ts) for ts in comparison.index]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=index, y=comparison["portfolio"], name="Portfolio", mode="lines",
                             line=dict(width=2, color=_SERIES_1, shape="hv"), hovertemplate="%{y:+.2%}"))
    if benchmark_symbol and comparison["benchmark"].notna().any():
        fig.add_trace(go.Scatter(x=index, y=comparison["benchmark"], name=f"{benchmark_symbol} buy & hold",
                                 mode="lines", line=dict(width=2, color=_SERIES_2, shape="hv"), hovertemplate="%{y:+.2%}"))
    fig.add_hline(y=0, line=dict(width=1, color="rgba(128,128,128,0.5)"))
    return _line_layout(fig, tickformat="+.0%")


# --- accesso ai dati --------------------------------------------------------------


@st.cache_resource
def _databases() -> DashboardDatabases:
    return dashboard_databases()


@st.cache_resource
def _launcher() -> SystemdJobLauncher:
    return SystemdJobLauncher()


@dataclass
class _PortfolioData:
    portfolio: PortfolioView
    positions: list[PositionView]
    watchlist: list[str]
    cash_history: list[CashPoint]
    changes: list[PositionChange]
    prices: list[PricePoint]
    latest: dict[int, PricePoint]
    decisions: list[DecisionView]
    runs: list[RunView]
    backtests: list[BacktestView]
    benchmark_symbol: str | None = None
    benchmark_prices: list[PricePoint] = field(default_factory=list)


def _load_portfolio(portfolio_id: int) -> _PortfolioData:
    with _databases().reader.database.transaction() as tx:
        repo = DashboardRepository(tx)
        portfolio = repo.portfolio(portfolio_id)
        positions = repo.positions(portfolio_id)
        changes = repo.position_changes(portfolio_id)
        since = portfolio.created_at - _PRICE_LOOKBACK
        held = sorted({c.asset_id for c in changes} | {p.asset_id for p in positions})
        data = _PortfolioData(
            portfolio=portfolio,
            positions=positions,
            watchlist=repo.watchlist(portfolio_id) if portfolio.is_model else [],
            cash_history=repo.cash_history(portfolio_id),
            changes=changes,
            prices=repo.prices(held, since=since),
            latest=repo.latest_prices([p.asset_id for p in positions]),
            decisions=repo.decisions(portfolio_id),
            runs=repo.runs(portfolio_id),
            backtests=repo.backtests(portfolio_id),
        )
        benchmark = repo.benchmark_asset()
        if benchmark is not None:
            data.benchmark_symbol = benchmark[1]
            data.benchmark_prices = repo.prices([benchmark[0]], since=since)
    return data


def _list_portfolios() -> list[PortfolioView]:
    with _databases().reader.database.transaction() as tx:
        return DashboardRepository(tx).portfolios()


def _select_portfolio(portfolios: list[PortfolioView], *, key: str, label: str = "Portfolio") -> PortfolioView:
    by_id = {p.portfolio_id: p for p in portfolios}
    selected = st.selectbox(label, options=list(by_id), format_func=lambda pid: by_id[pid].label, key=key)
    return by_id[selected]


def _flash(message: str, kind: str = "success") -> None:
    """Messaggio da mostrare dopo il `st.rerun()` che segue un'azione."""
    st.session_state.setdefault("_flash", []).append((kind, message))


def _show_flash() -> None:
    for kind, message in st.session_state.pop("_flash", []):
        getattr(st, kind)(message)


# --- pagina Portfolio -------------------------------------------------------------


def _portfolio_page() -> None:
    portfolios = _list_portfolios()
    if not portfolios:
        st.warning("Nessun portfolio trovato.")
        return
    with st.sidebar:
        selected = _select_portfolio(portfolios, key="portfolio_page")
    data = _load_portfolio(selected.portfolio_id)
    p = data.portfolio

    st.header(p.name)
    caption = f"{p.portfolio_type} · {'attivo' if p.is_active else 'sospeso'}"
    if p.is_model:
        caption += f" · {p.llm_provider} / {p.model_version}"
        if p.next_decision_at:
            caption += f" · prossimo ciclo {_local(p.next_decision_at):%d/%m %H:%M}"
    st.caption(caption)
    if p.is_model:
        with st.expander("Strategia"):
            st.write(p.strategy_prompt)

    curve = equity_curve(data.cash_history, data.changes, data.prices)
    summary = performance_summary(curve, starting_capital=p.starting_capital)
    comparison = benchmark_comparison(curve, p.starting_capital, data.benchmark_prices)
    benchmark_return = (
        comparison["benchmark"].iloc[-1] if not comparison.empty and comparison["benchmark"].notna().any() else None
    )

    cols = st.columns(5)
    cols[0].metric("Equity", _money(summary.equity), _pct(summary.total_return))
    cols[1].metric("Cash", _money(p.cash))
    cols[2].metric("Capitale iniziale", _money(p.starting_capital))
    cols[3].metric(f"{data.benchmark_symbol or 'Benchmark'} stesso periodo", _pct(benchmark_return))
    cols[4].metric("Max drawdown", _pct(summary.max_drawdown))
    st.caption(
        "Equity = cash + posizioni all'ultimo close noto (mark-to-market). "
        "Un asset senza prezzi vale il suo costo."
    )

    tabs = st.tabs(["Andamento", "Posizioni", "Decisioni", "Cicli", "Backtest"])
    with tabs[0]:
        if len(curve) >= 2:
            st.subheader("Rendimento contro il benchmark")
            st.plotly_chart(_comparison_chart(comparison, data.benchmark_symbol), use_container_width=True)
            st.subheader("Equity e cash")
            st.plotly_chart(_equity_chart(curve), use_container_width=True)
        elif not curve.empty:
            # Un solo punto non fa una curva: Plotly dedurrebbe un asse
            # temporale privo di senso.
            st.info(f"Un solo valore finora: {_money(summary.equity)}.")
        else:
            st.info("Nessuno storico ancora disponibile.")
    with tabs[1]:
        rows = valuation(data.positions, data.latest)
        if rows:
            st.dataframe(
                _valuation_dataframe(rows),
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Prezzo medio": st.column_config.NumberColumn(format="$%.2f"),
                    "Ultimo prezzo": st.column_config.NumberColumn(format="$%.2f"),
                    "Costo": st.column_config.NumberColumn(format="$%.2f"),
                    "Valore": st.column_config.NumberColumn(format="$%.2f"),
                    "P&L": st.column_config.NumberColumn(format="$%.2f"),
                    "P&L %": st.column_config.NumberColumn(format="percent"),
                    "Data prezzo": st.column_config.DatetimeColumn(format="DD/MM HH:mm"),
                },
            )
        else:
            st.info("Nessuna posizione aperta.")
        if p.is_model:
            st.subheader("Watchlist")
            if data.watchlist:
                st.write(", ".join(data.watchlist))
            else:
                st.info("Watchlist vuota: portfolio non ancora inizializzato (pagina Gestione).")
    with tabs[2]:
        if data.decisions:
            choice = st.multiselect("Decisione", ["BUY", "SELL", "HOLD"], default=["BUY", "SELL", "HOLD"])
            shown = [d for d in data.decisions if d.decision in choice]
            st.dataframe(_decisions_dataframe(shown), hide_index=True, use_container_width=True,
                         column_config={"Size %": st.column_config.NumberColumn(format="percent"),
                                        "Data": st.column_config.DatetimeColumn(format="DD/MM/YY HH:mm"),
                                        "Ricontrollo": st.column_config.DatetimeColumn(format="DD/MM/YY HH:mm")})
        else:
            st.info("Nessuna decisione ancora registrata.")
    with tabs[3]:
        if data.runs:
            last = data.runs[0]
            if last.status in ("failed", "partial"):
                notice = st.error if last.status == "failed" else st.warning
                notice(f"{_status_label(last.status)} — ultimo ciclo (run {last.run_id}): "
                       f"{_truncate(last.error_message or 'causa non registrata')}")
                if last.error_message and len(last.error_message) > _NOTICE_CHARS:
                    with st.expander("Errore completo"):
                        st.code(last.error_message, language=None, wrap_lines=True)
            st.dataframe(_runs_dataframe(data.runs), hide_index=True, use_container_width=True,
                         column_config={"Inizio ciclo": st.column_config.DatetimeColumn(format="DD/MM/YY HH:mm"),
                                        "Ultimo tentativo": st.column_config.DatetimeColumn(format="DD/MM/YY HH:mm")})
            st.caption("Esito n/d: run precedenti alla registrazione dell'esito (migrazione 0017).")
        else:
            st.info("Nessun ciclo ancora eseguito.")
    with tabs[4]:
        st.caption(
            "Rigioca l'intero storico di trade del portfolio su vectorbt e calcola le metriche dalla nascita "
            "del portfolio a oggi. Per un periodo diverso: "
            "`python -m marketmind_llm_decision_engine backtest <portfolio> --start AAAA-MM-GG --end AAAA-MM-GG`."
        )
        if st.button("Calcola backtest", key=f"backtest_{p.portfolio_id}"):
            _launch(lambda: _launcher().backtest(p.portfolio_id), "Backtest avviato")
            st.rerun()
        _show_flash()
        if data.backtests:
            st.dataframe(_backtests_dataframe(data.backtests), hide_index=True, use_container_width=True,
                         column_config={"Calcolato": st.column_config.DatetimeColumn(format="DD/MM/YY HH:mm"),
                                        "PnL": st.column_config.NumberColumn(format="$%.2f"),
                                        "Rendimento": st.column_config.NumberColumn(format="percent"),
                                        "SPY stesso periodo": st.column_config.NumberColumn(format="percent"),
                                        "Max drawdown": st.column_config.NumberColumn(format="percent"),
                                        "Win rate": st.column_config.NumberColumn(format="percent")})
        else:
            st.info("Nessun backtest per questo portfolio.")


# --- pagina Gestione --------------------------------------------------------------


def _role_notice() -> None:
    dbs = _databases()
    for role_db in (dbs.reader, dbs.admin):
        if role_db.fallback:
            st.warning(
                f"Password del ruolo `{role_db.role}` non configurata: uso le credenziali di POSTGRES_USER "
                f"(policy applicativa `{role_db.database.policy.name}` comunque applicata)."
            )


def _launch(action, success: str) -> str | None:
    try:
        unit = action()
    except JobLaunchError as exc:
        _flash(f"Job non avviato: {exc}", "error")
        return None
    _flash(f"{success} (`{unit}`): l'esito compare nei cicli/run quando il job termina.", "info")
    return unit


def _create_portfolio_form(portfolios: list[PortfolioView]) -> None:
    models = [p.model_version for p in portfolios if p.model_version]
    with st.form("new_portfolio", clear_on_submit=False):
        name = st.text_input("Nome")
        capital = st.number_input("Capitale iniziale ($)", min_value=0.0, value=10_000.0, step=1_000.0)
        provider = st.selectbox("Provider LLM", LLM_PROVIDERS)
        model = st.text_input("Modello", value=models[0] if models else "")
        strategy = st.text_area("Strategia", help="Persistente: entra nel contesto di ogni decisione.")
        active = st.checkbox("Attivo", value=True)
        init_now = st.checkbox(
            "Inizializza subito (l'LLM sceglie la watchlist e decide su ogni asset: consuma quota)", value=False
        )
        submitted = st.form_submit_button("Crea portfolio")
    if not submitted:
        return
    try:
        spec = NewModelPortfolio(name=name, starting_capital=capital, llm_provider=provider,
                                 model_version=model, strategy_prompt=strategy, is_active=active)
        with _databases().admin.database.transaction() as tx:
            portfolio_id = PortfolioAdmin(tx).create_model_portfolio(spec, now=datetime.now(timezone.utc))
    except (ValueError, LookupError) as exc:
        st.error(str(exc))
        return
    _flash(f"Portfolio {spec.name!r} creato (id {portfolio_id}).")
    if init_now:
        _launch(lambda: _launcher().init_portfolio(portfolio_id), "Bootstrap avviato")
    st.rerun()


def _manage_portfolio(p: PortfolioView) -> None:
    st.caption(
        f"{p.portfolio_type} · {'attivo' if p.is_active else 'sospeso'}"
        + (f" · {p.llm_provider} / {p.model_version}" if p.is_model else "")
        + (f" · prossimo ciclo {_local(p.next_decision_at):%d/%m/%Y %H:%M}" if p.next_decision_at else "")
    )
    admin_db = _databases().admin.database

    toggle = "Sospendi" if p.is_active else "Riattiva"
    if st.button(toggle, key=f"toggle_{p.portfolio_id}"):
        with admin_db.transaction() as tx:
            PortfolioAdmin(tx).set_active(p.portfolio_id, not p.is_active)
        _flash(f"{p.name}: {'sospeso' if p.is_active else 'riattivato'}.")
        st.rerun()
    if not p.is_model:
        st.info("Un benchmark non ha modello, strategia né cicli da gestire.")
        return

    st.markdown("**Job del Decision Engine** — consumano quota LLM.")
    confirm = st.checkbox("Confermo l'uso della quota LLM", key=f"confirm_{p.portfolio_id}")
    c1, c2 = st.columns(2)
    if c1.button("Nuovo ciclo ora", disabled=not confirm or not p.is_active, key=f"cycle_{p.portfolio_id}",
                 help="Rende il portfolio dovuto e avvia `run-due`: decisione su tutta la watchlist."):
        with admin_db.transaction() as tx:
            PortfolioAdmin(tx).request_new_cycle(p.portfolio_id, now=datetime.now(timezone.utc))
        _launch(_launcher().trigger_decision_run, "Giro del Decision Engine avviato")
        st.rerun()
    if c2.button("Inizializza (rigenera la watchlist)", disabled=not confirm, key=f"init_{p.portfolio_id}",
                 help="`init-portfolio`: sostituisce la watchlist e fa un primo giro di decisioni."):
        _launch(lambda: _launcher().init_portfolio(p.portfolio_id), "Bootstrap avviato")
        st.rerun()
    try:
        status = _launcher().status(INIT_PORTFOLIO_UNIT.format(portfolio_id=p.portfolio_id))
        if status.active_state != "inactive" or status.finished:
            st.caption(f"Ultimo bootstrap: {status.active_state} {status.result} {status.finished}".strip())
    except JobLaunchError:
        pass

    with st.form(f"settings_{p.portfolio_id}"):
        model = st.text_input("Modello", value=p.model_version or "")
        strategy = st.text_area("Strategia", value=p.strategy_prompt or "", height=160)
        if st.form_submit_button("Salva modello e strategia"):
            try:
                with admin_db.transaction() as tx:
                    PortfolioAdmin(tx).update_settings(p.portfolio_id, model_version=model, strategy_prompt=strategy)
            except ValueError as exc:
                st.error(str(exc))
            else:
                _flash(f"{p.name}: impostazioni salvate, valgono dal prossimo giro.")
                st.rerun()


def _admin_page() -> None:
    st.header("Gestione portfolio")
    _show_flash()
    _role_notice()
    portfolios = _list_portfolios()
    with st.expander("Nuovo portfolio model", expanded=not portfolios):
        _create_portfolio_form(portfolios)
    if portfolios:
        st.subheader("Portfolio esistenti")
        _manage_portfolio(_select_portfolio(portfolios, key="admin_page"))


# --- pagina Sistema ---------------------------------------------------------------


def _system_page() -> None:
    st.header("Sistema")
    _show_flash()
    with _databases().reader.database.transaction() as tx:
        latest = DashboardRepository(tx).latest_ingestion_runs()
    now = datetime.now(timezone.utc)

    st.subheader("Pipeline di ingestion — ultimo run")
    failed = [r for r in latest if r.status == "failed"]
    if failed:
        st.error("❌ Ultimo run fallito: " + ", ".join(f"{r.source} → {r.target_table}" for r in failed))
    if latest:
        st.dataframe(_ingestion_dataframe(latest, now), hide_index=True, use_container_width=True,
                     column_config={"Ultimo avvio": st.column_config.DatetimeColumn(format="DD/MM HH:mm")})
    else:
        st.info("Nessun run di ingestion registrato.")

    st.subheader("Avvio manuale")
    launcher = _launcher()
    try:
        pipelines = launcher.pipelines()
    except JobLaunchError as exc:
        st.warning(f"systemd non raggiungibile da questa macchina, avvio manuale non disponibile: {exc}")
        return
    c1, c2 = st.columns(2)
    with c1:
        pipeline = st.selectbox("Pipeline", pipelines)
        if st.button("Avvia pipeline", disabled=not pipelines):
            _launch(lambda: launcher.trigger_pipeline(pipeline), f"Pipeline {pipeline} avviata")
            st.rerun()
    with c2:
        st.caption("`run-due`: apre i cicli dovuti e riprende quelli incompleti, per tutti i portfolio attivi.")
        if st.button("Avvia giro del Decision Engine"):
            _launch(launcher.trigger_decision_run, "Giro del Decision Engine avviato")
            st.rerun()

    st.subheader("Stato delle unit")
    units = [DECIDE_UNIT] + [f"marketmind-ingest-{name}.service" for name in pipelines]
    statuses = [launcher.status(unit) for unit in units]
    st.dataframe(
        pd.DataFrame(
            [{"Unit": s.unit, "Stato": s.active_state, "Risultato": s.result, "Ultima fine": s.finished}
             for s in statuses]
        ),
        hide_index=True,
        use_container_width=True,
    )


def main() -> None:
    configure_logging()
    st.set_page_config(page_title="AI Market Mind", layout="wide")
    st.title("AI Market Mind")
    page = st.navigation(
        [
            st.Page(_portfolio_page, title="Portfolio", default=True),
            st.Page(_admin_page, title="Gestione", url_path="gestione"),
            st.Page(_system_page, title="Sistema", url_path="sistema"),
        ]
    )
    page.run()


if __name__ == "__main__":
    main()
