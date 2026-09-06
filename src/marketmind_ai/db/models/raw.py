"""Tabelle dello schema `raw`: payload grezzi separati dalle tabelle raffinate.

Rispecchia colonna per colonna l'`erDiagram` confermato in
`Market Mind AI - Docs/db/01_schema_dati_er.md` (vault Obsidian esterno al
repo, percorso in CLAUDE.md) — refactor del 05-09-26 (agenda #43), che
ribalta la decisione precedente di tenere `raw_payload` in JSONB inline
sulle tabelle raffinate (`Market Mind AI.md` §7).

Ogni tabella qui è in relazione 1:1 con una tabella raffinata di
`market_data` che in precedenza portava una colonna `raw_payload`
(`t_news_events`, `t_company_events`): la chiave primaria è la stessa
chiave surrogata della tabella raffinata, anche foreign key verso di essa
(`ON DELETE CASCADE` — un payload grezzo non ha senso senza la riga
raffinata a cui appartiene). `source`/`fetched_at` sono duplicate qui
rispetto alla tabella raffinata: ogni riga di `raw` resta così
autosufficiente per una query di retention/purge per età, senza bisogno di
un join. Deliberatamente escluse da `AUDITED_TABLES` (0001): sono dati di
solo insert, mai aggiornati, e un log generico delle loro cancellazioni per
retention non avrebbe valore (lo stesso principio già applicato a
`portfolio.t_portfolios`/`t_portfolio_positions` in 0002, per motivi
diversi).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from marketmind_ai.db.base import Base

SCHEMA = "raw"


class NewsEventRaw(Base):
    """Payload grezzo completo di una riga di `market_data.t_news_events`."""

    __tablename__ = "t_news_events_raw"
    __table_args__ = {"schema": SCHEMA}

    news_event_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("market_data.t_news_events.news_event_id", ondelete="CASCADE"),
        primary_key=True,
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)


class CompanyEventRaw(Base):
    """Payload grezzo completo di una riga di `market_data.t_company_events`."""

    __tablename__ = "t_company_events_raw"
    __table_args__ = {"schema": SCHEMA}

    company_event_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("market_data.t_company_events.company_event_id", ondelete="CASCADE"),
        primary_key=True,
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
