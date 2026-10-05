"""L'Historical Context Builder: per un asset, un portfolio e un `as_of`,
ricostruisce esattamente ciò che era noto fino a quel momento e lo
confeziona in un `DecisionContext` pronto per `LLMProvider.decide()`.

Le finestre stanno in `ContextWindows`, non nei repository: sono logica di
windowing, i repository restano accesso parametrico. Finestre strette per
v1 (poco storico, poche news) per contenere i token di prompt, sempre
parametriche: un futuro motore più agentico potrà sceglierle diversamente.

Lo stato del portfolio è letto per un solo `portfolio_id`: il builder non
può vedere lo stato di un portfolio diverso da quello per cui decide.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from marketmind_db.database import Transaction
from marketmind_llm_decision_engine.decision_engine.schemas import (
    AssetSummary,
    BootstrapContext,
    CompanyEventSnippet,
    DecisionContext,
    MacroSnippet,
    NewsSnippet,
    PortfolioPositionSnippet,
    PortfolioState,
    PricePoint,
)
from marketmind_llm_decision_engine.repositories.market_context import AssetRef, MarketContextRepository
from marketmind_llm_decision_engine.repositories.portfolio import PortfolioRepository


@dataclass(frozen=True)
class ContextWindows:
    price_days_back: int = 30
    news_days_back: int = 7
    news_max_items: int = 20
    company_event_days_back: int = 90

    def as_config(self) -> dict[str, int]:
        """Registrate su `t_model_runs.config` per audit e riproducibilità."""
        return asdict(self)


class ContextBuilder:
    def __init__(self, tx: Transaction, windows: ContextWindows = ContextWindows()) -> None:
        self.market = MarketContextRepository(tx)
        self.portfolios = PortfolioRepository(tx)
        self.windows = windows

    def build(self, asset: AssetRef, *, portfolio_id: int, as_of: datetime) -> DecisionContext:
        """Gli eventi macro non sono per asset (`t_macro_events` non ha
        `asset_id`) né hanno una finestra: l'ultimo valore noto a `as_of`."""
        w = self.windows
        prices = self.market.prices(asset.asset_id, as_of=as_of, days_back=w.price_days_back)
        news = self.market.news(
            asset.asset_id, as_of=as_of, days_back=w.news_days_back, max_items=w.news_max_items
        )
        company_events = self.market.company_events(
            asset.asset_id, as_of=as_of, days_back=w.company_event_days_back
        )
        macro = self.market.latest_macro(as_of=as_of)
        portfolio = self.portfolios.get(portfolio_id)
        positions = self.portfolios.positions(portfolio_id)

        return DecisionContext(
            asset_id=asset.asset_id,
            symbol=asset.symbol,
            as_of=as_of,
            portfolio=PortfolioState(
                portfolio_id=portfolio.portfolio_id,
                name=portfolio.name,
                cash=portfolio.cash,
                strategy_prompt=portfolio.strategy_prompt,
                positions=[
                    PortfolioPositionSnippet(symbol=p.symbol, quantity=p.quantity, avg_price=p.avg_price)
                    for p in positions
                ],
            ),
            prices=[PricePoint(ts=p.ts, close=p.close) for p in prices],
            news=[NewsSnippet(ts=n.ts, headline=n.headline, sentiment_score=n.sentiment_score) for n in news],
            macro_events=[
                MacroSnippet(indicator=m.indicator, ts=m.ts, value=m.value)
                for m in sorted(macro, key=lambda m: m.indicator)
            ],
            company_events=[CompanyEventSnippet(ts=e.ts, event_type=e.event_type) for e in company_events],
        )

    def build_bootstrap(self, portfolio_id: int) -> BootstrapContext:
        """Il contesto di `select_watchlist()`: l'universo intero (candidati
        per qualunque portfolio) più la strategia di questo portfolio."""
        portfolio = self.portfolios.get(portfolio_id)
        return BootstrapContext(
            portfolio_id=portfolio.portfolio_id,
            portfolio_name=portfolio.name,
            strategy_prompt=portfolio.strategy_prompt,
            universe=[
                AssetSummary(symbol=a.symbol, name=a.name, sector=a.sector, asset_type=a.asset_type)
                for a in self.market.decision_universe()
            ],
        )
