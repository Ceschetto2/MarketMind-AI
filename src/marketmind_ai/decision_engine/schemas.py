"""Il context package dell'Historical Context Builder.

Distinto da `llm/schemas.py` (`Decision`/`DeferralRequest`, l'output del
provider): `DecisionContext` è l'input, costruito qui e mai esposto a
`llm/`, che riceve solo il suo `model_dump(mode="json")` come `dict`
generico (`LLMProvider.decide(context: dict)`) — `llm/` non conosce questa
forma, per non invertire la direzione di dipendenza (`decision_engine/`
dipende da `llm/`, mai il contrario).

Ogni categoria porta pochi campi essenziali al prompt, non un rispecchiamento
1:1 delle tabelle di `market_data` — coerente con `context_snapshot`, che
salva esattamente questo `model_dump`, non le righe grezze.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


class PricePoint(BaseModel):
    ts: datetime
    close: float


class NewsSnippet(BaseModel):
    ts: datetime
    headline: str
    sentiment_score: Optional[float] = None


class MacroSnippet(BaseModel):
    indicator: str
    ts: date
    value: Optional[float] = None


class CompanyEventSnippet(BaseModel):
    ts: date
    event_type: str


class PortfolioPositionSnippet(BaseModel):
    symbol: str
    quantity: float
    avg_price: float


class PortfolioState(BaseModel):
    """Stato del portfolio per cui si sta decidendo — cash e posizioni
    correnti, non solo l'identità. La decisione è portfolio-aware: la stessa
    combinazione asset+timestamp può produrre un giudizio diverso per due
    portfolio diversi (uno ha capitale libero, l'altro è già esposto
    sull'asset). Nessun limite di allocazione/concentrazione qui dentro
    ancora — `Market Mind AI - Docs/Decision Engine/`, non deciso oggi."""

    portfolio_id: int
    name: str
    cash: float
    positions: list[PortfolioPositionSnippet]


class DecisionContext(BaseModel):
    """Il context package passato a `LLMProvider.decide()` per un asset, per
    un portfolio specifico.

    `as_of` è il momento rispetto a cui il context builder ha applicato il
    filtro no-look-ahead (`ts <= as_of` su ogni query) — di norma l'istante
    corrente, ma esplicito e parametrizzabile per permettere in futuro sia
    un rinvio (`llm.schemas.DeferralRequest`) sia una simulazione storica
    (Backtesting Engine, non ancora scritto). `portfolio` è isolato per
    disegno: costruito leggendo solo lo stato di *questo* portfolio, mai di
    altri (`db/portfolio_reader.py`).
    """

    asset_id: int
    symbol: str
    as_of: datetime
    portfolio: PortfolioState
    prices: list[PricePoint]
    news: list[NewsSnippet]
    macro_events: list[MacroSnippet]
    company_events: list[CompanyEventSnippet]
