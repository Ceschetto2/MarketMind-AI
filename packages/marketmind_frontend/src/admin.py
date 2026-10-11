"""Gestione dei portfolio dalla dashboard: creazione, sospensione, modello e
strategia, richiesta di un nuovo ciclo.

Le uniche scritture della dashboard, con la policy `APP` (ruolo
`marketmind_app`, lo stesso del Decision Engine) e solo su
`portfolio.t_portfolios`. Tutto ciò che chiama l'LLM o scambia — bootstrap
della watchlist, decisioni, trade — resta al Decision Engine, avviato come
job (`jobs`), mai eseguito dentro la dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from marketmind_db.database import Transaction
from marketmind_db.models.portfolio import Portfolio

# I nomi accettati da `llm.factory.get_provider` del Decision Engine. Copia
# voluta: la dashboard non dipende da quel pacchetto. Un provider nuovo va
# aggiunto in entrambi i punti.
LLM_PROVIDERS = ("gemini",)


def _required_text(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field} obbligatorio")
    return value


@dataclass(frozen=True)
class NewModelPortfolio:
    """Un portfolio `model` da creare. Nasce senza watchlist: va inizializzato
    (`init-portfolio`) perché il motore abbia asset su cui decidere."""

    name: str
    starting_capital: float
    llm_provider: str
    model_version: str
    strategy_prompt: str
    is_active: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required_text(self.name, "nome"))
        object.__setattr__(self, "model_version", _required_text(self.model_version, "modello"))
        object.__setattr__(self, "strategy_prompt", _required_text(self.strategy_prompt, "strategia"))
        if not self.starting_capital > 0:
            raise ValueError("il capitale iniziale deve essere positivo")
        if self.llm_provider not in LLM_PROVIDERS:
            raise ValueError(f"provider LLM sconosciuto: {self.llm_provider!r} (disponibili: {LLM_PROVIDERS})")


class PortfolioAdmin:
    def __init__(self, tx: Transaction) -> None:
        self.tx = tx
        self._portfolios = tx.repository(Portfolio)

    def create_model_portfolio(self, spec: NewModelPortfolio, *, now: datetime) -> int:
        """Crea il portfolio con tutto il capitale in cash. `next_decision_at`
        resta NULL: il motore lo considera dovuto al primo giro utile."""
        if self._portfolios.get_one(name=spec.name) is not None:
            raise ValueError(f"un portfolio di nome {spec.name!r} esiste già")
        [row] = self._portfolios.insert_returning(
            [
                {
                    "name": spec.name,
                    "portfolio_type": "model",
                    "starting_capital": spec.starting_capital,
                    "cash": spec.starting_capital,
                    "created_at": now,
                    "is_active": spec.is_active,
                    "llm_provider": spec.llm_provider,
                    "model_version": spec.model_version,
                    "strategy_prompt": spec.strategy_prompt,
                }
            ],
            returning=("portfolio_id",),
        )
        return row["portfolio_id"]

    def set_active(self, portfolio_id: int, active: bool) -> None:
        """Sospende o riattiva: un portfolio sospeso è ignorato da `run-due`,
        ma il suo stato e il suo storico restano."""
        self._update(portfolio_id, {"is_active": active})

    def update_settings(
        self, portfolio_id: int, *, model_version: str | None = None, strategy_prompt: str | None = None
    ) -> None:
        """Cambia modello e/o strategia di un portfolio `model`; valgono dal
        prossimo giro. La watchlist non cambia: per riceglierla serve un
        nuovo `init-portfolio`."""
        self._model(portfolio_id)
        values: dict[str, Any] = {}
        if model_version is not None:
            values["model_version"] = _required_text(model_version, "modello")
        if strategy_prompt is not None:
            values["strategy_prompt"] = _required_text(strategy_prompt, "strategia")
        if values:
            self._update(portfolio_id, values)

    def request_new_cycle(self, portfolio_id: int, *, now: datetime) -> None:
        """Rende il portfolio dovuto subito: al prossimo `run-due` il motore
        apre un ciclo nuovo su tutta la watchlist, invece di aspettare la
        cadenza. Il ciclo in corso si chiude lì, come a scadenza naturale."""
        self._model(portfolio_id)
        self._update(portfolio_id, {"next_decision_at": now})

    def _model(self, portfolio_id: int) -> Portfolio:
        portfolio = self._get(portfolio_id)
        if portfolio.portfolio_type != "model":
            raise ValueError(f"il portfolio {portfolio.name!r} non è di tipo 'model'")
        return portfolio

    def _get(self, portfolio_id: int) -> Portfolio:
        portfolio = self._portfolios.get_one(portfolio_id=portfolio_id)
        if portfolio is None:
            raise LookupError(f"portfolio {portfolio_id} inesistente")
        return portfolio

    def _update(self, portfolio_id: int, values: dict[str, Any]) -> None:
        self._get(portfolio_id)
        self._portfolios.update(values, where={"portfolio_id": portfolio_id})
