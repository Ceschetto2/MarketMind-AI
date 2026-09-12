"""L'Historical Context Builder: per un asset e un `as_of`, ricostruisce
esattamente ciò che era noto fino a quel momento e lo confeziona in un
`DecisionContext` pronto per `LLMProvider.decide()`.

Le finestre di default sono qui, non in `db/context_reader.py`: quella è
logica di windowing, responsabilità di questo modulo — `db/` resta un
puro strato di accesso parametrico. Finestre "strette" per v1 (poco
storico, poche news), scelta deliberata per contenere i token di prompt su
~500 asset/settimana; ogni finestra resta un parametro esplicito di
`build_context()` così un futuro Decision Engine più agentico potrà
sceglierle diversamente per asset o per richiesta, senza cambiare firma.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from marketmind_ai.db.context_reader import (
    get_latest_macro_events,
    get_recent_company_events,
    get_recent_news,
    get_recent_prices,
)
from marketmind_ai.decision_engine.schemas import (
    CompanyEventSnippet,
    DecisionContext,
    MacroSnippet,
    NewsSnippet,
    PricePoint,
)

DEFAULT_PRICE_DAYS_BACK = 30
DEFAULT_NEWS_DAYS_BACK = 7
DEFAULT_NEWS_MAX_ITEMS = 20
DEFAULT_COMPANY_EVENT_DAYS_BACK = 90


def build_context(
    session: Session,
    asset_id: int,
    symbol: str,
    as_of: datetime | None = None,
    price_days_back: int = DEFAULT_PRICE_DAYS_BACK,
    news_days_back: int = DEFAULT_NEWS_DAYS_BACK,
    news_max_items: int = DEFAULT_NEWS_MAX_ITEMS,
    company_event_days_back: int = DEFAULT_COMPANY_EVENT_DAYS_BACK,
) -> DecisionContext:
    """Costruisce il `DecisionContext` di `asset_id` a `as_of` (default: ora).

    Gli eventi macro non sono filtrati per asset (`t_macro_events` non ha
    `asset_id`, per disegno — un indicatore come `UNRATE` non appartiene a
    un singolo titolo) e non hanno una finestra propria: è sempre l'ultimo
    valore noto per indicatore a `as_of`, coerente con l'uso di ALFRED lato
    ingestion.
    """
    as_of = as_of or datetime.now(timezone.utc)

    prices = get_recent_prices(session, asset_id, as_of=as_of, days_back=price_days_back)
    news = get_recent_news(
        session, asset_id, as_of=as_of, days_back=news_days_back, max_items=news_max_items
    )
    macro_events = get_latest_macro_events(session, as_of=as_of)
    company_events = get_recent_company_events(
        session, asset_id, as_of=as_of, days_back=company_event_days_back
    )

    return DecisionContext(
        asset_id=asset_id,
        symbol=symbol,
        as_of=as_of,
        prices=[PricePoint(ts=p.ts, close=p.close) for p in prices],
        news=[
            NewsSnippet(ts=n.ts, headline=n.headline, sentiment_score=n.sentiment_score)
            for n in news
        ],
        macro_events=[
            MacroSnippet(indicator=m.indicator, ts=m.ts, value=m.value) for m in macro_events
        ],
        company_events=[
            CompanyEventSnippet(ts=e.ts, event_type=e.event_type) for e in company_events
        ],
    )
