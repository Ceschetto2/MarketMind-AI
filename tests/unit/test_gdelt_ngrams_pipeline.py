"""Test unitari per la logica pura di `gdelt_ngrams_pipeline.py`.

Nessun accesso a rete/DB reale: `requests.get` è mockato con `pytest-mock`.
I file reali (`ngrams.txt.gz`/`toc.json.gz`) contengono milioni di righe —
qui si usano contenuti gzip fittizi ma nel formato reale (poche righe).
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone

import pytest
import requests

from marketmind_ai.ingestion.gdelt_ngrams_pipeline import (
    _build_records,
    _candidate_timestamps,
    _download_gz,
    _link_docids_to_symbols,
    _match_symbol,
    _parse_ngrams,
    _parse_toc,
)
from marketmind_ai.schemas import NewsEventRecord

UNIVERSE = [("AAPL", "Apple Inc."), ("MSFT", "Microsoft Corporation"), ("V", "Visa Inc.")]


class TestCandidateTimestamps:
    def test_starts_five_minutes_before_now_and_goes_backwards(self):
        now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)

        candidates = _candidate_timestamps(now, count=3)

        assert candidates == ["20260912115500", "20260912115400", "20260912115300"]

    def test_default_count_is_reasonable(self):
        now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)

        candidates = _candidate_timestamps(now)

        assert len(candidates) >= 5


class TestDownloadGz:
    def test_returns_decompressed_text_on_200(self, mocker):
        content = gzip.compress(b"riga di prova")
        mock_response = mocker.Mock(status_code=200, content=content)
        mock_response.raise_for_status.return_value = None
        mocker.patch(
            "marketmind_ai.ingestion.gdelt_ngrams_pipeline.requests.get",
            return_value=mock_response,
        )

        result = _download_gz("https://example.com/file.gz")

        assert result == "riga di prova"

    def test_returns_none_on_404_without_raising(self, mocker):
        """Un 404 è normale (molti minuti non hanno file pubblicato), non un
        errore — trattato esplicitamente, non lasciato propagare come
        eccezione."""
        mock_response = mocker.Mock(status_code=404)
        mocker.patch(
            "marketmind_ai.ingestion.gdelt_ngrams_pipeline.requests.get",
            return_value=mock_response,
        )

        result = _download_gz("https://example.com/file.gz")

        assert result is None

    def test_retries_on_connection_error_then_succeeds(self, mocker):
        content = gzip.compress(b"contenuto")
        mock_response = mocker.Mock(status_code=200, content=content)
        mock_response.raise_for_status.return_value = None
        mock_get = mocker.patch(
            "marketmind_ai.ingestion.gdelt_ngrams_pipeline.requests.get",
            side_effect=[
                requests.exceptions.ConnectionError("rete non raggiungibile"),
                mock_response,
            ],
        )
        mocker.patch.object(_download_gz.retry, "sleep", lambda _seconds: None)

        result = _download_gz("https://example.com/file.gz")

        assert result == "contenuto"
        assert mock_get.call_count == 2


class TestParseNgrams:
    def test_parses_tab_delimited_lines(self):
        text = "1\tApple reported strong\t3\n2\tsome other quadgram\t1\n"

        rows = _parse_ngrams(text)

        assert rows == [
            ("1", "Apple reported strong", "3"),
            ("2", "some other quadgram", "1"),
        ]

    def test_skips_malformed_lines(self):
        text = "1\tApple reported strong\t3\nriga malformata senza tab\n3\tok quadgram qui\t1\n"

        rows = _parse_ngrams(text)

        assert len(rows) == 2

    def test_empty_text_returns_empty_list(self):
        assert _parse_ngrams("") == []


class TestParseToc:
    def test_parses_json_lines_keyed_by_id(self):
        text = "\n".join(
            [
                json.dumps({"ID": 1, "date": "2026-09-12", "title": "T1", "lang": "en", "img": "", "url": "https://a"}),
                json.dumps({"ID": 2, "date": "2026-09-12", "title": "T2", "lang": "en", "img": "", "url": "https://b"}),
            ]
        )

        toc = _parse_toc(text)

        assert set(toc) == {"1", "2"}
        assert toc["1"]["title"] == "T1"

    def test_empty_text_returns_empty_dict(self):
        assert _parse_toc("") == {}


class TestMatchSymbol:
    """Entity linking: match sul ticker (parola intera, case-sensitive — i
    ticker sono convenzionalmente in maiuscolo nel testo) o sulla prima
    parola distintiva del nome azienda (case-insensitive)."""

    def test_matches_on_ticker_as_whole_word(self):
        assert _match_symbol("shares of V rose today", "V", "Visa Inc.") == True

    def test_does_not_match_ticker_as_substring_of_another_word(self):
        # "V" non deve matchare dentro "Value" o "Very".
        assert _match_symbol("Very strong quarter results", "V", "Visa Inc.") == False

    def test_matches_on_company_name_token_case_insensitive(self):
        assert _match_symbol("apple reported strong earnings", "AAPL", "Apple Inc.") == True

    def test_no_match_returns_false(self):
        assert _match_symbol("completely unrelated text here", "AAPL", "Apple Inc.") == False


class TestLinkDocidsToSymbols:
    def test_links_first_match_per_docid(self):
        ngrams = [
            ("1", "Apple reported strong", "3"),
            ("2", "unrelated quadgram text", "1"),
            ("3", "Microsoft announced new", "2"),
        ]

        result = _link_docids_to_symbols(ngrams, UNIVERSE)

        assert result == {"1": "AAPL", "3": "MSFT"}
        assert "2" not in result

    def test_no_matches_returns_empty_dict(self):
        ngrams = [("1", "nothing relevant here", "1")]

        assert _link_docids_to_symbols(ngrams, UNIVERSE) == {}


class TestBuildRecords:
    def test_builds_news_event_record_per_matched_docid(self):
        toc = {
            "1": {"date": "2026-09-12", "title": "Apple news", "lang": "en", "url": "https://a"},
        }
        docid_to_symbol = {"1": "AAPL"}

        records = _build_records(toc, docid_to_symbol)

        assert len(records) == 1
        record = records[0]
        assert isinstance(record, NewsEventRecord)
        assert record.source == "GDELT-ngrams"
        assert record.symbol == "AAPL"
        assert record.headline == "Apple news"
        assert record.url == "https://a"
        assert record.raw_payload["title"] == "Apple news"

    def test_docid_without_toc_entry_is_skipped(self):
        """`ID` in toc.json.gz e `DOCID` in ngrams.txt.gz non sono sempre
        confrontabili 1:1 (nota nell'onboarding) — un DOCID linkato senza
        corrispondente in toc va scartato silenziosamente, non sollevare."""
        docid_to_symbol = {"999": "AAPL"}

        records = _build_records({}, docid_to_symbol)

        assert records == []
