"""Test unitari per la logica pura di `finnhub_news_pipeline.py`.

Nessun accesso a rete/DB reale: `requests.get`, `get_api_key`, `get_session`
e lo strato di scrittura (`db/writer.py`) sono mockati con `pytest-mock`.
`get_universe_symbols()` non è coperta qui (tocca il DB) — è compito del
test di integrazione.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import requests

from marketmind_ai.db.writer import AssetNotFoundError
from marketmind_ai.ingestion.finnhub_news_pipeline import (
    _articles_to_records,
    _fetch_company_news,
    run,
)
from marketmind_ai.schemas import NewsEventRecord


def _make_article(**overrides) -> dict:
    article = {
        "category": "company",
        "datetime": 1_756_339_200,  # 2025-08-28T00:00:00Z
        "headline": "Apple announces something",
        "id": 123,
        "image": "https://example.com/img.png",
        "related": "AAPL",
        "source": "Reuters",
        "summary": "A summary.",
        "url": "https://example.com/article-123",
    }
    article.update(overrides)
    return article


class TestArticlesToRecords:
    def test_maps_fields_and_converts_types(self):
        articles = [_make_article(), _make_article(id=456, url="https://example.com/456")]

        records = _articles_to_records("AAPL", articles)

        assert len(records) == 2
        assert all(isinstance(r, NewsEventRecord) for r in records)

        first = records[0]
        assert first.source == "Finnhub"
        assert first.symbol == "AAPL"
        assert first.headline == "Apple announces something"
        assert first.url == "https://example.com/article-123"
        assert first.sentiment_score is None
        assert first.ts == datetime.fromtimestamp(1_756_339_200, tz=timezone.utc)
        assert first.raw_payload == articles[0]

    def test_empty_articles_returns_empty_list(self):
        records = _articles_to_records("AAPL", [])

        assert records == []


class TestFetchCompanyNewsRetry:
    """`_fetch_company_news` è decorata con `tenacity.retry` (stop_after_attempt(4),
    wait_exponential reale fino a 30s). Azzeriamo `sleep` sull'oggetto
    `Retrying` attaccato alla funzione decorata invece di toccare la
    configurazione del decoratore in produzione — il conteggio dei
    tentativi resta quello vero.
    """

    def test_retries_and_eventually_succeeds(self, mocker):
        expected_articles = [_make_article()]
        mock_response = mocker.Mock()
        mock_response.json.return_value = expected_articles
        mock_response.raise_for_status.return_value = None

        mock_get = mocker.patch(
            "marketmind_ai.ingestion.finnhub_news_pipeline.requests.get",
            side_effect=[
                requests.exceptions.ConnectionError("rete non raggiungibile"),
                requests.exceptions.ConnectionError("rete non raggiungibile"),
                mock_response,
            ],
        )
        mocker.patch(
            "marketmind_ai.ingestion.finnhub_news_pipeline.get_api_key",
            return_value="fake-key",
        )
        mocker.patch.object(_fetch_company_news.retry, "sleep", lambda _seconds: None)

        result = _fetch_company_news("AAPL", "2026-08-01", "2026-08-03")

        assert result == expected_articles
        assert mock_get.call_count == 3

    def test_propagates_after_stop_after_attempt(self, mocker):
        mocker.patch(
            "marketmind_ai.ingestion.finnhub_news_pipeline.requests.get",
            side_effect=requests.exceptions.ConnectionError("rete non raggiungibile"),
        )
        mocker.patch(
            "marketmind_ai.ingestion.finnhub_news_pipeline.get_api_key",
            return_value="fake-key",
        )
        mocker.patch.object(_fetch_company_news.retry, "sleep", lambda _seconds: None)

        with pytest.raises(requests.exceptions.ConnectionError):
            _fetch_company_news("AAPL", "2026-08-01", "2026-08-03")


class TestRun:
    """Flusso di alto livello di `run()`, con tutte le dipendenze esterne
    (universo, fetch, sessione DB, strato di scrittura, audit) mockate."""

    def test_asset_not_found_for_one_symbol_does_not_block_the_others(self, mocker):
        mocker.patch(
            "marketmind_ai.ingestion.finnhub_news_pipeline.get_universe_symbols",
            return_value=["AAA", "BBB"],
        )
        # niente attese reali tra i ticker
        mocker.patch("marketmind_ai.ingestion.finnhub_news_pipeline.time.sleep")

        articles_aaa = [_make_article(id=1, url="https://example.com/1")]
        articles_bbb = [_make_article(id=2, url="https://example.com/2")]
        mocker.patch(
            "marketmind_ai.ingestion.finnhub_news_pipeline._fetch_company_news",
            side_effect=[articles_aaa, articles_bbb],
        )

        mock_session = mocker.MagicMock(name="session")
        mock_get_session = mocker.patch(
            "marketmind_ai.ingestion.finnhub_news_pipeline.get_session"
        )
        mock_get_session.return_value.__enter__.return_value = mock_session

        # AAA non ancora in t_assets: la pipeline logga e passa a BBB senza
        # interrompersi (comportamento sotto test).
        mock_resolve_asset_id = mocker.patch(
            "marketmind_ai.ingestion.finnhub_news_pipeline.resolve_asset_id",
            side_effect=[AssetNotFoundError("AAA non trovato in t_assets"), 42],
        )
        mock_write_news_event = mocker.patch(
            "marketmind_ai.ingestion.finnhub_news_pipeline.write_news_event"
        )

        tracker = mocker.Mock(rows_written=0)
        mock_ingestion_run = mocker.patch(
            "marketmind_ai.ingestion.finnhub_news_pipeline.ingestion_run"
        )
        mock_ingestion_run.return_value.__enter__.return_value = tracker

        run()

        assert mock_resolve_asset_id.call_count == 2
        # solo BBB arriva alla scrittura: AAA è stato saltato dopo l'eccezione.
        assert mock_write_news_event.call_count == 1
        args, _ = mock_write_news_event.call_args
        assert args[0] is mock_session
        assert args[1] == 42
        assert args[2].url == "https://example.com/2"
        assert tracker.rows_written == 1

    def test_symbol_with_no_articles_is_skipped_without_touching_db(self, mocker):
        mocker.patch(
            "marketmind_ai.ingestion.finnhub_news_pipeline.get_universe_symbols",
            return_value=["AAA"],
        )
        mocker.patch("marketmind_ai.ingestion.finnhub_news_pipeline.time.sleep")
        mocker.patch(
            "marketmind_ai.ingestion.finnhub_news_pipeline._fetch_company_news",
            return_value=[],
        )
        mock_resolve_asset_id = mocker.patch(
            "marketmind_ai.ingestion.finnhub_news_pipeline.resolve_asset_id"
        )
        mock_write_news_event = mocker.patch(
            "marketmind_ai.ingestion.finnhub_news_pipeline.write_news_event"
        )

        tracker = mocker.Mock(rows_written=0)
        mock_ingestion_run = mocker.patch(
            "marketmind_ai.ingestion.finnhub_news_pipeline.ingestion_run"
        )
        mock_ingestion_run.return_value.__enter__.return_value = tracker

        run()

        mock_resolve_asset_id.assert_not_called()
        mock_write_news_event.assert_not_called()
        assert tracker.rows_written == 0
