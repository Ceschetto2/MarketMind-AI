"""Test unitari di `finnhub_news_pipeline.py`: `HttpSource` è un mock."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from marketmind_pipelines.finnhub_news_pipeline import FinnhubNewsPipeline, articles_to_records
from marketmind_pipelines.http import HttpSource
from marketmind_pipelines.records import NewsEventRecord

_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _article(**overrides) -> dict:
    article = {
        "category": "company",
        "datetime": 1788000000,
        "headline": "Apple annuncia qualcosa",
        "id": 1,
        "image": "",
        "related": "AAPL",
        "source": "Reuters",
        "summary": "...",
        "url": "https://example.com/1",
    }
    article.update(overrides)
    return article


@pytest.fixture
def http(mocker):
    return mocker.MagicMock(spec=HttpSource)


class TestArticlesToRecords:
    def test_mappa_campi_e_converte_i_tipi(self):
        article = _article()

        [record] = articles_to_records("AAPL", [article], _NOW)

        assert isinstance(record, NewsEventRecord)
        assert record.source == "Finnhub"
        assert record.symbol == "AAPL"
        assert record.ts == datetime.fromtimestamp(1788000000, tz=timezone.utc)
        assert record.headline == "Apple annuncia qualcosa"
        assert record.url == "https://example.com/1"
        assert record.raw_payload == article
        assert record.sentiment_score is None

    def test_nessun_articolo(self):
        assert articles_to_records("AAPL", [], _NOW) == []


class TestPipeline:
    def test_extract_chiede_la_finestra_di_48_ore(self, http, mocker):
        mocker.patch(
            "marketmind_pipelines.finnhub_news_pipeline.datetime",
            mocker.Mock(now=lambda tz: _NOW, fromtimestamp=datetime.fromtimestamp),
        )
        http.get_json.return_value = []
        pipeline = FinnhubNewsPipeline(db=None, http=http, symbols=["AAPL"])

        pipeline.setup()
        pipeline.extract("AAPL")

        http.get_json.assert_called_once_with(
            "/company-news", params={"symbol": "AAPL", "from": "2026-09-04", "to": "2026-09-06"}
        )

    def test_client_di_default_con_token_finnhub(self):
        http = FinnhubNewsPipeline(db=None).http

        assert (http.base_url, http.api_key_env, http.api_key_param) == (
            "https://finnhub.io/api/v1", "FINNHUB_API_KEY", "token",
        )
