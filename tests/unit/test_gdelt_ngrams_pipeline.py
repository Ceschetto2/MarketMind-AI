"""Test unitari di `gdelt_ngrams_pipeline.py`: logica pura e `fetch`/`parse`.

Nessun accesso a rete/DB reale: `HttpSource` è un mock.
I file reali (`ngrams.txt.gz`/`toc.json.gz`) contengono milioni di righe —
qui si usano contenuti gzip fittizi ma nel formato reale (poche righe).
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone

import pytest
import requests

from marketmind_pipelines.gdelt_ngrams_pipeline import (
    CANDIDATE_MINUTES,
    GdeltFiles,
    GdeltNgramsPipeline,
    build_records,
    candidate_timestamps,
    link_docids_to_symbols,
    match_symbol,
    parse_ngrams,
    parse_toc,
)
from marketmind_pipelines.http import HttpSource
from marketmind_pipelines.records import NewsEventRecord

_NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
UNIVERSE = [("AAPL", "Apple Inc."), ("MSFT", "Microsoft Corporation"), ("V", "Visa Inc.")]


class TestCandidateTimestamps:
    def test_starts_five_minutes_before_now_and_goes_backwards(self):
        now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)

        candidates = candidate_timestamps(now, count=3)

        assert candidates == ["20260912115500", "20260912115400", "20260912115300"]

    def test_default_count_is_reasonable(self):
        now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)

        candidates = candidate_timestamps(now)

        assert len(candidates) >= 5


class TestFetch:
    """`fetch()`: primo minuto candidato con un file pubblicato; 404 = minuto
    senza file, normale e non un errore."""

    def _response(self, content: bytes):
        response = requests.Response()
        response.status_code = 200
        response._content = gzip.compress(content)
        return response

    def test_salta_i_minuti_senza_file_e_scarica_ngrams_e_toc(self, mocker):
        http = mocker.MagicMock(spec=HttpSource)
        http.get.side_effect = [None, self._response(b"ngrams"), self._response(b"toc")]

        files = GdeltNgramsPipeline(db=None, http=http).fetch()

        assert files == GdeltFiles(ngrams="ngrams", toc="toc")
        paths = [c.args[0] for c in http.get.call_args_list]
        assert paths[1].endswith(".ngrams.txt.gz") and paths[2].endswith(".toc.json.gz")
        assert paths[1].removesuffix(".ngrams.txt.gz") == paths[2].removesuffix(".toc.json.gz")
        assert all(c.kwargs["allow_404"] for c in http.get.call_args_list)

    def test_nessun_file_nella_finestra_restituisce_none(self, mocker):
        http = mocker.MagicMock(spec=HttpSource)
        http.get.return_value = None

        pipeline = GdeltNgramsPipeline(db=None, http=http)

        assert pipeline.fetch() is None
        assert pipeline.parse(None) == []
        assert http.get.call_count == CANDIDATE_MINUTES

    def test_parse_linka_gli_articoli_allUniverso(self):
        pipeline = GdeltNgramsPipeline(db=None, http=None)
        pipeline.universe = UNIVERSE
        toc = json.dumps({"ID": 1, "date": "2026-09-12", "title": "Apple news", "url": "https://a"})

        records = pipeline.parse(GdeltFiles(ngrams="1\tApple reported strong\t3\n", toc=toc))

        assert [(r.symbol, r.url) for r in records] == [("AAPL", "https://a")]


class TestParseNgrams:
    def test_parses_tab_delimited_lines(self):
        text = "1\tApple reported strong\t3\n2\tsome other quadgram\t1\n"

        rows = parse_ngrams(text)

        assert rows == [
            ("1", "Apple reported strong", "3"),
            ("2", "some other quadgram", "1"),
        ]

    def test_skips_malformed_lines(self):
        text = "1\tApple reported strong\t3\nriga malformata senza tab\n3\tok quadgram qui\t1\n"

        rows = parse_ngrams(text)

        assert len(rows) == 2

    def test_empty_text_returns_empty_list(self):
        assert parse_ngrams("") == []


class TestParseToc:
    def test_parses_json_lines_keyed_by_id(self):
        text = "\n".join(
            [
                json.dumps({"ID": 1, "date": "2026-09-12", "title": "T1", "lang": "en", "img": "", "url": "https://a"}),
                json.dumps({"ID": 2, "date": "2026-09-12", "title": "T2", "lang": "en", "img": "", "url": "https://b"}),
            ]
        )

        toc = parse_toc(text)

        assert set(toc) == {"1", "2"}
        assert toc["1"]["title"] == "T1"

    def test_empty_text_returns_empty_dict(self):
        assert parse_toc("") == {}


class TestMatchSymbol:
    """Entity linking: match sul ticker (parola intera, case-sensitive — i
    ticker sono convenzionalmente in maiuscolo nel testo) o sulla prima
    parola distintiva del nome azienda (case-insensitive)."""

    def test_matches_on_ticker_as_whole_word(self):
        assert match_symbol("shares of V rose today", "V", "Visa Inc.") == True

    def test_does_not_match_ticker_as_substring_of_another_word(self):
        # "V" non deve matchare dentro "Value" o "Very".
        assert match_symbol("Very strong quarter results", "V", "Visa Inc.") == False

    def test_matches_on_company_name_token_case_insensitive(self):
        assert match_symbol("apple reported strong earnings", "AAPL", "Apple Inc.") == True

    def test_no_match_returns_false(self):
        assert match_symbol("completely unrelated text here", "AAPL", "Apple Inc.") == False


class TestLinkDocidsToSymbols:
    def test_links_first_match_per_docid(self):
        ngrams = [
            ("1", "Apple reported strong", "3"),
            ("2", "unrelated quadgram text", "1"),
            ("3", "Microsoft announced new", "2"),
        ]

        result = link_docids_to_symbols(ngrams, UNIVERSE)

        assert result == {"1": "AAPL", "3": "MSFT"}
        assert "2" not in result

    def test_no_matches_returns_empty_dict(self):
        ngrams = [("1", "nothing relevant here", "1")]

        assert link_docids_to_symbols(ngrams, UNIVERSE) == {}


class TestBuildRecords:
    def test_builds_news_event_record_per_matched_docid(self):
        toc = {
            "1": {"date": "2026-09-12", "title": "Apple news", "lang": "en", "url": "https://a"},
        }
        docid_to_symbol = {"1": "AAPL"}

        records = build_records(toc, docid_to_symbol, _NOW)

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

        records = build_records({}, docid_to_symbol, _NOW)

        assert records == []
