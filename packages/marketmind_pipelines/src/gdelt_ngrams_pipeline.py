"""Pipeline `gdelt-ngrams`: news da GDELT Web NGrams, con entity linking.

Cadenza ogni 15 minuti (timer `marketmind-ingest-gdelt-ngrams.timer`),
allineata al battito nativo della pipeline GDELT.

A differenza della DOC API (1 richiesta/5s, impraticabile su un intero
universo — vedi `02_gdelt_onboarding.md`), Web NGrams pubblica due file
gzippati per minuto, senza API key né rate limit applicativo:
`ngrams.txt.gz` (`DOCID`, `QUADGRAM`, `COUNT`) e `toc.json.gz` (metadati
articolo per `ID`). Si scarica `ngrams.txt.gz` una sola volta e lo si
scansiona localmente con un match testuale coi nomi/ticker dell'universo —
una scansione copre tutti gli asset insieme — poi si incrociano i `DOCID`
trovati con `toc.json.gz` per titolo/data/URL.

Molti minuti non hanno file pubblicato: un 404 è normale, non un errore —
si provano alcuni timestamp consecutivi a partire da 5 minuti fa. Nessun
file nella finestra = run `success` con zero righe, non un fallimento.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from marketmind_pipelines.base import BulkPipeline
from marketmind_pipelines.http import HttpSource
from marketmind_pipelines.lookups import universe_assets
from marketmind_pipelines.records import NewsEventRecord
from marketmind_pipelines.sinks import NewsEventSink

logger = logging.getLogger(__name__)

SOURCE = "GDELT-ngrams"  # valore di NewsEventRecord.source (00_schema_interfacce.md)
BASE_URL = "https://storage.googleapis.com/data.gdeltproject.org/gdeltv5/weblegacy/ngrams"
CANDIDATE_MINUTES = 10
USER_AGENT = "Mozilla/5.0 (compatible; AIMarketMind/0.1)"

_WORD_RE = re.compile(r"[A-Za-z]+")


def candidate_timestamps(now: datetime, count: int = CANDIDATE_MINUTES) -> list[str]:
    """Timestamp minuto per minuto, a partire da 5 minuti fa (raccomandazione
    GDELT sulla latenza di pubblicazione) e andando indietro — la pipeline
    eredita un battito ogni 15 minuti, quindi non tutti i minuti hanno file:
    più candidati aumentano la probabilità di trovarne uno pubblicato.
    """
    start = now - timedelta(minutes=5)
    return [
        (start - timedelta(minutes=i)).strftime("%Y%m%d%H%M00") for i in range(count)
    ]


def parse_ngrams(text: str) -> list[tuple[str, str, str]]:
    rows = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def parse_toc(text: str) -> dict[str, dict]:
    if not text:
        return {}
    toc = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        toc[str(record["ID"])] = record
    return toc


def primary_name_token(name: str) -> str:
    """Prima parola del nome azienda, ripulita — es. "Apple Inc." -> "apple".
    Euristica semplice, non un vero NLP (deciso, vedi CLAUDE.md): non
    distingue "Apple" azienda da "apple" frutto, un compromesso accettato
    per questa prima fase.
    """
    match = _WORD_RE.search(name)
    return match.group(0).lower() if match else name.lower()


def match_symbol(quadgram: str, symbol: str, name: str) -> bool:
    """Entity linking testuale: ticker come parola intera (case-sensitive —
    i ticker sono convenzionalmente in maiuscolo nel testo giornalistico,
    riduce falsi positivi su ticker corti come "V"/"A") oppure la prima
    parola distintiva del nome azienda (case-insensitive). Non un vero
    servizio NLP — deciso, vedi `02_gdelt_onboarding.md`.
    """
    words = _WORD_RE.findall(quadgram)
    if symbol in words:
        return True
    name_token = primary_name_token(name)
    return any(word.lower() == name_token for word in words)


def link_docids_to_symbols(
    ngrams: list[tuple[str, str, str]], universe: list[tuple[str, str]]
) -> dict[str, str]:
    """DOCID -> symbol, primo match vince: un articolo si collega a un solo
    asset (semplificazione — `NewsEventRecord.symbol` porta un solo
    ticker), non a tutti quelli eventualmente citati insieme.
    """
    linked: dict[str, str] = {}
    for docid, quadgram, _count in ngrams:
        if docid in linked:
            continue
        for symbol, name in universe:
            if match_symbol(quadgram, symbol, name):
                linked[docid] = symbol
                break
    return linked


def build_records(
    toc: dict[str, dict], docid_to_symbol: dict[str, str], fetched_at: datetime
) -> list[NewsEventRecord]:
    """Un DOCID linkato senza corrispondente in `toc` viene scartato: `ID`
    (toc) e `DOCID` (ngrams) non sono sempre confrontabili 1:1 (nota
    nell'onboarding) — non è un errore da sollevare.
    """
    records = []
    for docid, symbol in docid_to_symbol.items():
        toc_record = toc.get(docid)
        if toc_record is None:
            continue
        records.append(
            NewsEventRecord(
                source=SOURCE,
                ts=datetime.fromisoformat(toc_record["date"]).replace(tzinfo=timezone.utc),
                symbol=symbol,
                headline=toc_record["title"],
                raw_payload=toc_record,
                sentiment_score=None,
                url=toc_record["url"],
                fetched_at=fetched_at,
            )
        )
    return records


@dataclass(frozen=True)
class GdeltFiles:
    ngrams: str
    toc: str


class GdeltNgramsPipeline(BulkPipeline[GdeltFiles | None, NewsEventRecord]):
    name = "gdelt-ngrams"
    audit_source = "gdelt-ngrams"
    target_table = "market_data.t_news_events"
    sink = NewsEventSink

    def __init__(self, db, *, http: HttpSource | None = None) -> None:
        super().__init__(db)
        self.http = http or HttpSource(BASE_URL, headers={"User-Agent": USER_AGENT})

    def setup(self) -> None:
        with self.db.transaction() as tx:
            self.universe = universe_assets(tx)

    def fetch(self) -> GdeltFiles | None:
        for ts in candidate_timestamps(datetime.now(timezone.utc)):
            ngrams = self._download(f"/{ts}.ngrams.txt.gz")
            if ngrams is None:
                continue
            return GdeltFiles(ngrams=ngrams, toc=self._download(f"/{ts}.toc.json.gz") or "")
        logger.info("nessun file pubblicato nella finestra di candidati, nulla da fare")
        return None

    def parse(self, raw: GdeltFiles | None) -> list[NewsEventRecord]:
        if raw is None:
            return []
        ngrams = parse_ngrams(raw.ngrams)
        records = build_records(
            parse_toc(raw.toc), link_docids_to_symbols(ngrams, self.universe), datetime.now(timezone.utc)
        )
        logger.info("%d righe ngrams, %d articoli linkati all'universo", len(ngrams), len(records))
        return records

    def _download(self, path: str) -> str | None:
        response = self.http.get(path, allow_404=True)
        if response is None:
            return None
        return gzip.decompress(response.content).decode("utf-8", errors="replace")
