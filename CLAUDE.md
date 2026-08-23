# CLAUDE.md

Istruzioni di progetto per Claude Code su **AI Market Mind**.

## Cos'è il progetto

Piattaforma che simula decisioni finanziarie storiche (BUY/SELL/HOLD) tramite un LLM, usando solo le informazioni disponibili fino a un timestamp storico — nessun look-ahead — e ne backtesta l'esito su un universo di ~500 asset (S&P 500). Documento di riferimento completo: `Market Mind AI - Docs/Market Mind AI.md`.

## Stato attuale

Onboarding e test delle 5 API dati completati (chiavi salvate localmente — **mai committarle**, vanno in `podman secret` o in un `.env` escluso da git). Non esiste ancora codice applicativo. Il prossimo passo secondo la roadmap è formalizzare lo schema.

## Stack

- **Linguaggio**: Python
- **Database**: PostgreSQL + TimescaleDB — un solo livello di tabelle normalizzate, niente bronze/silver
- **Validazione dati**: Pydantic
- **ORM/migrazioni**: SQLAlchemy + Alembic
- **Backtesting**: vectorbt, con `cash_sharing=True` per simulare un unico portafoglio
- **Dashboard**: Streamlit, connessa direttamente a Postgres via SQLAlchemy
- **Orchestrazione**: nessun tool dedicato — script Python + timer systemd
- **Container**: Podman rootless via systemd Quadlet; target futuro: server NixOS con `virtualisation.oci-containers`. Per il momento sviluppo localhost.
- **Provider LLM**: non ancora deciso (OpenAI / Anthropic / modello locale). Per iniziare creiamo una interfaccia generica e una implementazione con le api di gemini.

## Decisioni architetturali da rispettare
- Ogni script di ingestion deve produrre output conforme a una delle interfacce Pydantic in `Market Mind AI - Docs/Data Providers/00_schema_interfacce.md` (`AssetRecord`, `MarketPriceRecord`, `NewsEventRecord`, `MacroEventRecord`, `CompanyEventRecord`) prima di scrivere su Postgres — mai il payload grezzo direttamente.
- Le interfacce di ingestion usano `symbol` come identificatore naturale, non `asset_id`: la risoluzione all'id interno avviene nello strato di scrittura/upsert, non nello script di ingestion.
- GDELT: usare il dataset Web NGrams per la copertura ampia sui 500 asset, non la DOC API — il rate limit di 1 richiesta/5s la rende impraticabile a quella scala. Dettagli in `Market Mind AI - Docs/Data Providers/02_gdelt_onboarding.md`.

## Fonti dati

Un file per fonte in `Market Mind AI - Docs/Data Providers/` (yfinance, GDELT, Finnhub, FRED, Financial Modeling Prep), ciascuno con endpoint usati, schema della risposta grezza, limiti dell'endpoint e interfaccia Pydantic di destinazione.

## Schema dati

Cinque tabelle: `assets`, `market_prices` (hypertable), `news_events`, `macro_events`, `company_events`. Dettaglio in `Market Mind AI - Docs/Market Mind AI.md` §6.

## Decisioni ancora aperte — non assumere, segnalare l'ambiguità

Motore di backtest da confermare (vectorbt raccomandato), provider LLM, cadenza delle decisioni (probabilmente non giornaliera su 500 asset), granularità temporale dei prezzi, orizzonte storico di backfill, logica di windowing dell'Historical Context Builder, entity linking GDELT → asset, retention strategy.

## Convenzioni di documentazione

I documenti `.md` di progetto (non il codice) seguono uno stile a prosa continua, con tabelle solo per contenuto genuinamente tabulare — evitare elenchi puntati esagerati quando si aggiorna `Market Mind AI - Docs/Market Mind AI.md` o i file in `Market Mind AI - Docs/Data Providers/`. Usare marmaid per grafici.