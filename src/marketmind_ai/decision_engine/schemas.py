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


class DecisionContext(BaseModel):
    """Il context package passato a `LLMProvider.decide()` per un asset.

    `as_of` è il momento rispetto a cui il context builder ha applicato il
    filtro no-look-ahead (`ts <= as_of` su ogni query) — di norma l'istante
    corrente, ma esplicito e parametrizzabile per permettere in futuro sia
    un rinvio (`llm.schemas.DeferralRequest`) sia una simulazione storica
    (Backtesting Engine, non ancora scritto).
    """

    asset_id: int
    symbol: str
    as_of: datetime
    prices: list[PricePoint]
    news: list[NewsSnippet]
    macro_events: list[MacroSnippet]
    company_events: list[CompanyEventSnippet]
