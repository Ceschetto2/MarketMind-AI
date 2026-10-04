"""Registro delle pipeline: nome (`BasePipeline.name`) → classe.

È l'elenco che l'entry point (`python -m marketmind_pipelines run <nome>`)
e le unit Quadlet usano; il nome coincide con il suffisso del container
(`marketmind-ingest-<nome>`).
"""

from __future__ import annotations

from marketmind_pipelines.base import BasePipeline
from marketmind_pipelines.finnhub_earnings_pipeline import FinnhubEarningsPipeline
from marketmind_pipelines.finnhub_news_pipeline import FinnhubNewsPipeline
from marketmind_pipelines.fmp_pipeline import FmpPipeline
from marketmind_pipelines.fred_pipeline import FredPipeline
from marketmind_pipelines.gdelt_ngrams_pipeline import GdeltNgramsPipeline
from marketmind_pipelines.universe_csv_pipeline import UniverseCsvPipeline
from marketmind_pipelines.yfinance_assets_pipeline import YFinanceAssetsPipeline
from marketmind_pipelines.yfinance_prices_pipeline import YFinancePricesPipeline

PIPELINES: dict[str, type[BasePipeline]] = {
    cls.name: cls
    for cls in (
        UniverseCsvPipeline,
        YFinanceAssetsPipeline,
        YFinancePricesPipeline,
        FinnhubNewsPipeline,
        FinnhubEarningsPipeline,
        FmpPipeline,
        FredPipeline,
        GdeltNgramsPipeline,
    )
}
