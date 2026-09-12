"""Pipeline `gdelt-ngrams`: news da GDELT Web NGrams, con entity linking.

Entry point standalone, invocato dal container Quadlet
`marketmind-ingest-gdelt-ngrams.container` — cadenza ogni 15-30 minuti (già
decisa, allineata al battito nativo della pipeline GDELT).

A differenza della DOC API (1 richiesta/5s, impraticabile su un intero
universo — non usata, vedi `02_gdelt_onboarding.md`), Web NGrams pubblica
due file gzippati per minuto su GCS, senza API key né rate limit
applicativo: `ngrams.txt.gz` (`DOCID`, `QUADGRAM`, `COUNT`) e `toc.json.gz`
(metadati articolo per `ID`). Il flusso: si scarica `ngrams.txt.gz` una
sola volta e si scansiona localmente per un match testuale coi nomi/ticker
dell'universo — una singola scansione copre tutti gli asset insieme, non
una richiesta per asset — poi si incrociano i `DOCID` trovati con
`toc.json.gz` per titolo/data/URL.

Molti minuti non hanno file pubblicato: un 404 è normale, non un errore —
si prova un piccolo numero di timestamp consecutivi, a partire da 5 minuti
fa (raccomandazione GDELT per la latenza di pubblicazione).
"""

from __future__ import annotations

import gzip
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from marketmind_ai.db.models.market_data import Asset, UniverseMember
from marketmind_ai.db.session import get_session
from marketmind_ai.db.writer import AssetNotFoundError, ingestion_run, resolve_asset_id, write_news_event
from marketmind_ai.schemas import NewsEventRecord
from marketmind_ai.utils.logging_config import configure_logging
from sqlalchemy import select

logger = logging.getLogger(__name__)

SOURCE = "GDELT-ngrams"  # valore di NewsEventRecord.source (00_schema_interfacce.md)
AUDIT_SOURCE = "gdelt-ngrams"  # valore ammesso da ck_t_ingestion_runs_source (minuscolo)
TARGET_TABLE = "market_data.t_news_events"

_BASE_URL = "https://storage.googleapis.com/data.gdeltproject.org/gdeltv5/weblegacy/ngrams"
_CANDIDATE_MINUTES = 10
_USER_AGENT = "Mozilla/5.0 (compatible; AIMarketMind/0.1)"

_WORD_RE = re.compile(r"[A-Za-z]+")


def _candidate_timestamps(now: datetime, count: int = _CANDIDATE_MINUTES) -> list[str]:
    """Timestamp minuto per minuto, a partire da 5 minuti fa (raccomandazione
    GDELT sulla latenza di pubblicazione) e andando indietro — la pipeline
    eredita un battito ogni 15 minuti, quindi non tutti i minuti hanno file:
    più candidati aumentano la probabilità di trovarne uno pubblicato.
    """
    start = now - timedelta(minutes=5)
    return [
        (start - timedelta(minutes=i)).strftime("%Y%m%d%H%M00") for i in range(count)
    ]


@retry(
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def _download_gz(url: str) -> str | None:
    """Scarica e decomprime un file gzip. `None` su 404 (minuto senza file
    pubblicato, normale) — non un'eccezione. Altri errori HTTP/di rete
    passano dal retry di `tenacity`.
    """
    response = requests.get(url, headers={"User-Agent": _USER_AGENT})
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return gzip.decompress(response.content).decode("utf-8", errors="replace")


def _parse_ngrams(text: str) -> list[tuple[str, str, str]]:
    rows = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def _parse_toc(text: str) -> dict[str, dict]:
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


def _primary_name_token(name: str) -> str:
    """Prima parola del nome azienda, ripulita — es. "Apple Inc." -> "apple".
    Euristica semplice, non un vero NLP (deciso, vedi CLAUDE.md): non
    distingue "Apple" azienda da "apple" frutto, un compromesso accettato
    per questa prima fase.
    """
    match = _WORD_RE.search(name)
    return match.group(0).lower() if match else name.lower()


def _match_symbol(quadgram: str, symbol: str, name: str) -> bool:
    """Entity linking testuale: ticker come parola intera (case-sensitive —
    i ticker sono convenzionalmente in maiuscolo nel testo giornalistico,
    riduce falsi positivi su ticker corti come "V"/"A") oppure la prima
    parola distintiva del nome azienda (case-insensitive). Non un vero
    servizio NLP — deciso, vedi `02_gdelt_onboarding.md`.
    """
    words = _WORD_RE.findall(quadgram)
    if symbol in words:
        return True
    name_token = _primary_name_token(name)
    return any(word.lower() == name_token for word in words)


def _link_docids_to_symbols(
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
            if _match_symbol(quadgram, symbol, name):
                linked[docid] = symbol
                break
    return linked


def _build_records(toc: dict[str, dict], docid_to_symbol: dict[str, str]) -> list[NewsEventRecord]:
    """Un DOCID linkato senza corrispondente in `toc` viene scartato: `ID`
    (toc) e `DOCID` (ngrams) non sono sempre confrontabili 1:1 (nota
    nell'onboarding) — non è un errore da sollevare.
    """
    fetched_at = datetime.now(timezone.utc)
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


def _universe_names_and_symbols() -> list[tuple[str, str]]:
    with get_session() as session:
        return list(
            session.execute(
                select(Asset.symbol, Asset.name).join(
                    UniverseMember, UniverseMember.asset_id == Asset.asset_id
                )
            ).all()
        )


def run() -> None:
    universe = _universe_names_and_symbols()
    now = datetime.now(timezone.utc)

    ngrams_text = toc_text = None
    for ts in _candidate_timestamps(now):
        ngrams_text = _download_gz(f"{_BASE_URL}/{ts}.ngrams.txt.gz")
        if ngrams_text is None:
            continue
        toc_text = _download_gz(f"{_BASE_URL}/{ts}.toc.json.gz")
        break

    if ngrams_text is None:
        logger.info("nessun file pubblicato nella finestra di candidati, nulla da fare")
        return

    ngrams = _parse_ngrams(ngrams_text)
    toc = _parse_toc(toc_text or "")
    docid_to_symbol = _link_docids_to_symbols(ngrams, universe)
    records = _build_records(toc, docid_to_symbol)
    logger.info("%d righe ngrams, %d articoli linkati all'universo", len(ngrams), len(records))

    with ingestion_run(AUDIT_SOURCE, TARGET_TABLE) as tracker:
        with get_session() as session:
            for record in records:
                try:
                    asset_id = resolve_asset_id(session, record.symbol)
                except AssetNotFoundError:
                    logger.warning("%s non ancora in t_assets, salto", record.symbol)
                    continue
                write_news_event(session, asset_id, record)
                tracker.rows_written += 1

    logger.info("completato: %d righe scritte", tracker.rows_written)


if __name__ == "__main__":
    configure_logging()
    run()
