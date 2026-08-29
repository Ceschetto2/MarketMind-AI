# CLAUDE.md

Istruzioni di progetto per Claude Code su **AI Market Mind**.

## Dove si trova la documentazione di progetto

Ogni riferimento a `Market Mind AI - Docs/...` in questo file (ER diagram, interfacce Pydantic, doc di onboarding per fonte, agenda delle decisioni) punta a una cartella **esterna a questo repository git**, non presente nel checkout: è un vault Obsidian su filesystem locale. Il percorso risolto è:

```
/home/francesco/Documents/Obsidian/Conoscenza/3 - Castello/Progetti Personali/Market Mind AI - Docs
```

Il modo canonico per trovarlo è aprire `MarketMind-AI.code-workspace` in root (gitignored, non versionato: è configurazione locale dell'editor, non del progetto) — dichiara due cartelle radice, `MarketMind-AI` (questo repo) e `Docs` (il percorso sopra). Se il file workspace non fosse presente o il percorso fosse cambiato, è lì che va cercato per primo prima di assumere che la documentazione non esista.

## Cos'è il progetto

Piattaforma che simula decisioni finanziarie storiche (BUY/SELL/HOLD) tramite un LLM, usando solo le informazioni disponibili fino a un timestamp storico — nessun look-ahead — e ne backtesta l'esito su un universo di ~500 asset (S&P 500). Documento di riferimento completo: `Market Mind AI - Docs/Market Mind AI.md`.

## Stato attuale

Onboarding e test delle 5 API dati completati (chiavi salvate localmente — **mai committarle**, vanno in `podman secret` o in un `.env` escluso da git). `experiments/` contiene script usa-e-getta per scaricare campioni grezzi da ciascuna fonte (output in `data/samples/`, escluso da git), usati per validare i campi osservati nei doc di onboarding. Lo schema dati (ER diagram, naming conventions, indicizzazione) è formalizzato in `Market Mind AI - Docs/Architettura/01_schema_dati_er.md`; la struttura delle cartelle in `Market Mind AI - Docs/Architettura/00_struttura_cartelle.md`. Il modulo `db/` (modelli SQLAlchemy, naming convention espliciti, session factory) e la prima migrazione Alembic sono implementati, allineati a quell'ER diagram. Il deploy locale del container Postgres+TimescaleDB è scriptato in `deploy/quadlet/deploy.sh` (idempotente, non interattivo se `MARKETMIND_DB_PASSWORD` è in ambiente). Non ancora implementati: `schemas/` (le 5 interfacce Pydantic), `ingestion/`, `llm/`, `decision_engine/`, `backtest/`, `dashboard/`, `orchestration/`.

## Stack

- **Linguaggio**: Python
- **Database**: PostgreSQL + TimescaleDB — un solo livello di tabelle normalizzate, niente bronze/silver
- **Validazione dati**: Pydantic
- **ORM/migrazioni**: SQLAlchemy + Alembic
- **Backtesting**: vectorbt, con `cash_sharing=True` per simulare un unico portafoglio
- **Dashboard**: Streamlit, connessa direttamente a Postgres via SQLAlchemy
- **Orchestrazione**: nessun tool dedicato — script Python + timer systemd
- **Container**: Podman rootless via systemd Quadlet, deploy automatizzato da `deploy/quadlet/deploy.sh`; target futuro: server NixOS con `virtualisation.oci-containers`. Per il momento sviluppo localhost. Direzione CI/CD (GitHub Actions) ancora aperta, vedi `Market Mind AI - Docs/Architettura/02_ci_cd.md`.
- **Provider LLM**: Gemini per la prima implementazione, dietro un'interfaccia generica (`llm/base.py`); nuovi provider si aggiungono come moduli affiancati senza toccare `decision_engine/`.

## Decisioni architetturali da rispettare

- Ogni script di ingestion deve produrre output conforme a una delle interfacce Pydantic in `Market Mind AI - Docs/Data Providers/00_schema_interfacce.md` (`AssetRecord`, `MarketPriceRecord`, `NewsEventRecord`, `MacroEventRecord`, `CompanyEventRecord`) prima di scrivere su Postgres — mai il payload grezzo direttamente.
- Le interfacce di ingestion usano `symbol`/`indicator` come identificatore naturale, non `asset_id`: la risoluzione all'id interno avviene nello strato di scrittura/upsert in `db/`, non negli script di ingestion.
- GDELT: usare il dataset Web NGrams per la copertura ampia sui 500 asset, non la DOC API — il rate limit di 1 richiesta/5s la rende impraticabile a quella scala. Dettagli in `Market Mind AI - Docs/Data Providers/02_gdelt_onboarding.md`.
- Naming conventions di schema (nomi tabella/colonna/vincolo/indice) sono fissate in `Market Mind AI - Docs/Architettura/01_schema_dati_er.md` § Naming conventions — in particolare la `naming_convention` esplicita su `MetaData` (`pk_<table>`, `fk_<table>_<column>_<ref_table>`, `uq_<table>_<column>`, `ck_<table>_<constraint_name>`) e il prefisso `i<access_method>_` per gli indici creati esplicitamente. Non introdurre nomi impliciti generati da Postgres/SQLAlchemy.

## Fonti dati

Un file per fonte in `Market Mind AI - Docs/Data Providers/` (yfinance, GDELT, Finnhub, FRED, Financial Modeling Prep), ciascuno con endpoint usati, schema della risposta grezza, limiti dell'endpoint e interfaccia Pydantic di destinazione.

## Schema dati

Dieci tabelle in tre schema Postgres separati: `market_data` (`t_assets`, `t_market_prices` hypertable, `t_news_events`, `t_macro_events`, `t_company_events`), `decisions` (`t_model_runs`, `t_model_decisions`, `t_backtest_results`), `audit` (`t_ingestion_runs`, `t_audit_logs` — logging e auditing operativo). Nessuna delle tabelle di ingestion conosce strutturalmente le fonti dati: la provenienza vive solo in `source`/`fetched_at`, e `raw_payload` in JSONB dove serve preservare il payload originale (non su `t_market_prices`, che è già completamente tipizzata) — mai nella struttura delle colonne, così da poter sostituire o aggiungere fonti senza modificare lo schema. `t_audit_logs` è popolata da un trigger Postgres generico, non dallo strato applicativo. ER diagram completo, tipi, chiavi e indicizzazione in `Market Mind AI - Docs/Architettura/01_schema_dati_er.md`.

## Decisioni prese il 29-08-26 (revisione interattiva, dettaglio completo in `Market Mind AI - Docs/agenda.md`)

Motore di backtest: vectorbt confermato. Cadenza delle decisioni: settimanale su tutti i 500 asset in una prima fase, con trigger event-driven da valutare in futuro in base a costi e risultati osservati. Granularità temporale dei prezzi: intraday (oraria). Orizzonte storico di backfill: nessuno — si accumula in avanti da quando il sistema è online, su tutte le fonti. Entity linking GDELT → asset: match su nome azienda/ticker estratto dal `QUADGRAM` di Web NGrams. Dati macro FRED: API ALFRED (vintage), non le serie standard, per coerenza col principio no-look-ahead. Retention strategy: safeguard di un anno per ora, da rivedere in base a test futuri. `t_audit_logs` popolata da trigger Postgres, non dallo strato applicativo. `run_ts`/`decision_ts` normalizzati a `ts`; PK di `t_market_prices` estesa a `(asset_id, ts, source)`.

## Decisioni ancora aperte — non assumere, segnalare l'ambiguità

Logica di windowing dell'Historical Context Builder e definizione delle skill/tool esposte all'LLM per estenderlo a runtime (richiesta di più storico, eventi di aziende correlate): impatta `llm/` e `decision_engine/`, richiede un provider con function calling/tool-use affidabile (agenda #10, #21). Automazione CI/CD del deploy container via GitHub Actions: runner self-hosted sul server di destinazione vs. runner GitHub-hosted con deploy via SSH, gestione dei secret nel workflow, eventuale build custom delle immagini — bloccata a monte dall'assenza di un server di destinazione diverso da `localhost` (agenda #22, #23, #24; dettaglio in `Market Mind AI - Docs/Architettura/02_ci_cd.md`). Promemoria operativi non bloccanti: verificare se il reset dei 250 req/giorno del piano free FMP è giornaliero o su finestra mobile (agenda #16); vincolo di licenza "uso non commerciale" del piano free Finnhub, da monitorare se lo scope del progetto cambiasse (agenda #18).

## Convenzioni di documentazione

I documenti `.md` di progetto (non il codice) seguono uno stile a prosa continua, con tabelle solo per contenuto genuinamente tabulare — evitare elenchi puntati esagerati quando si aggiorna `Market Mind AI - Docs/Market Mind AI.md` o i file in `Market Mind AI - Docs/Data Providers/`. Usare mermaid per i grafici.
