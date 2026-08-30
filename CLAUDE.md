# CLAUDE.md

Istruzioni di progetto per Claude Code su **AI Market Mind**.

## Dove si trova la documentazione di progetto

Ogni riferimento a `Market Mind AI - Docs/...` in questo file (ER diagram, interfacce Pydantic, doc di onboarding per fonte, agenda delle decisioni) punta a una cartella **esterna a questo repository git**, non presente nel checkout: è un vault Obsidian su filesystem locale. Il percorso risolto è:

```
/home/francesco/Documents/Obsidian/Conoscenza/3 - Castello/Progetti Personali/Market Mind AI - Docs
```

Il modo canonico per trovarlo è aprire `MarketMind-AI.code-workspace` in root (gitignored, non versionato: è configurazione locale dell'editor, non del progetto) — dichiara due cartelle radice, `MarketMind-AI` (questo repo) e `Docs` (il percorso sopra). Se il file workspace non fosse presente o il percorso fosse cambiato, è lì che va cercato per primo prima di assumere che la documentazione non esista.

## Workflow git per le sessioni Claude Code

Le sessioni Claude Code che lavorano su questo repo — tipicamente isolate in un worktree separato (`.claude/worktrees/...`) — non pushano mai i propri branch su `origin`/GitHub e non aprono Pull Request su GitHub: il lavoro resta locale. A fine modifiche i commit restano sul branch locale del worktree, già visibile senza bisogno di fetch (i worktree condividono lo stesso `.git`); è l'utente a integrarli nel proprio branch quando vuole, tipicamente con `git merge <branch-worktree>` dal proprio checkout. Nota tecnica per una sessione futura che leggesse questo file: l'harness impedisce a una sessione isolata in un worktree di eseguire comandi git che toccano la cartella condivisa (il checkout principale dell'utente) — quindi anche se richiesto esplicitamente, una sessione così isolata non può eseguire il merge direttamente nel checkout dell'utente; può solo lasciare il branch pronto, committato, e comunicare all'utente il comando da lanciare lui stesso.

## Cos'è il progetto

Piattaforma che simula decisioni finanziarie storiche (BUY/SELL/HOLD) tramite un LLM, usando solo le informazioni disponibili fino a un timestamp storico — nessun look-ahead — e ne backtesta l'esito su un universo di ~500 asset (S&P 500). Documento di riferimento completo: `Market Mind AI - Docs/Market Mind AI.md`.

## Stato attuale

Onboarding e test delle 5 API dati completati (chiavi salvate localmente — **mai committarle**, vanno in `podman secret` o in un `.env` escluso da git). `experiments/` contiene script usa-e-getta per scaricare campioni grezzi da ciascuna fonte (output in `data/samples/`, escluso da git), usati per validare i campi osservati nei doc di onboarding. Lo schema dati (ER diagram, naming conventions, indicizzazione) è formalizzato in `Market Mind AI - Docs/Architettura/01_schema_dati_er.md`; la struttura delle cartelle in `Market Mind AI - Docs/Architettura/00_struttura_cartelle.md`. Il modulo `db/` (modelli SQLAlchemy, naming convention espliciti, session factory) e due migrazioni Alembic sono implementati, allineati a quell'ER diagram, con la config di Alembic in `pyproject.toml` (niente `alembic.ini`). Il deploy locale del container Postgres+TimescaleDB è scriptato in `deploy/quadlet/deploy.sh` (idempotente, non interattivo se `MARKETMIND_DB_PASSWORD` è in ambiente) ed è stato effettivamente eseguito il 29-08-26: la migrazione `0001_initial_schema` è applicata e verificata contro un'istanza reale (10 tabelle, hypertable, retention policy e trigger di audit testati, non solo validati offline). La migrazione `0002_portfolio_tracking`, che aggiunge le 4 tabelle del tracking del portafoglio virtuale in un proprio schema dedicato, `portfolio` (non `decisions`, refactor del 30-08-26 — vedi sotto), è stata applicata e verificata allo stesso modo il 30-08-26 (tabelle, CHECK/FK/indici e i due trigger dedicati `fn_portfolio_snapshot`/`fn_portfolio_position_snapshot` esercitati end-to-end contro l'istanza reale). Il logging condiviso (`marketmind_ai.logging_config.configure_logging()`) esiste ed è in uso da `alembic/env.py`. Non ancora implementati: `schemas/` (le 5 interfacce Pydantic), `ingestion/`, `llm/`, `decision_engine/`, `backtest/`, `dashboard/`, `orchestration/`.

## Stack

- **Linguaggio**: Python
- **Database**: PostgreSQL + TimescaleDB — un solo livello di tabelle normalizzate, niente bronze/silver
- **Validazione dati**: Pydantic
- **ORM/migrazioni**: SQLAlchemy + Alembic
- **Backtesting**: vectorbt, con `cash_sharing=True` per simulare un unico portafoglio
- **Dashboard**: Streamlit, connessa direttamente a Postgres via SQLAlchemy
- **Orchestrazione**: nessun tool dedicato — script Python + timer systemd
- **Container**: Podman rootless via systemd Quadlet, deploy automatizzato da `deploy/quadlet/deploy.sh`; target futuro: server NixOS con `virtualisation.oci-containers`. Per il momento sviluppo localhost. Rete Podman condivisa `marketmind.network` (deciso il 30-08-26, vedi sotto e `Market Mind AI - Docs/Architettura/05_networking.md`) per far comunicare più container sullo stesso host — pronta ma non ancora agganciata a `marketmind-db.container`, in attesa di un riavvio della macchina (agenda #36). Direzione CI/CD (GitHub Actions) ancora aperta, vedi `Market Mind AI - Docs/Architettura/02_ci_cd.md`.
- **Provider LLM**: Gemini per la prima implementazione, dietro un'interfaccia generica (`llm/base.py`); nuovi provider si aggiungono come moduli affiancati senza toccare `decision_engine/`.

## Decisioni architetturali da rispettare

- Ogni script di ingestion deve produrre output conforme a una delle interfacce Pydantic in `Market Mind AI - Docs/Data Providers/00_schema_interfacce.md` (`AssetRecord`, `MarketPriceRecord`, `NewsEventRecord`, `MacroEventRecord`, `CompanyEventRecord`) prima di scrivere su Postgres — mai il payload grezzo direttamente.
- Le interfacce di ingestion usano `symbol`/`indicator` come identificatore naturale, non `asset_id`: la risoluzione all'id interno avviene nello strato di scrittura/upsert in `db/`, non negli script di ingestion.
- GDELT: usare il dataset Web NGrams per la copertura ampia sui 500 asset, non la DOC API — il rate limit di 1 richiesta/5s la rende impraticabile a quella scala. Dettagli in `Market Mind AI - Docs/Data Providers/02_gdelt_onboarding.md`.
- Naming conventions di schema (nomi tabella/colonna/vincolo/indice) sono fissate in `Market Mind AI - Docs/Architettura/01_schema_dati_er.md` § Naming conventions — in particolare la `naming_convention` esplicita su `MetaData` (`pk_<table>`, `fk_<table>_<column>_<ref_table>`, `uq_<table>_<column>`, `ck_<table>_<constraint_name>`) e il prefisso `i<access_method>_` per gli indici creati esplicitamente. Non introdurre nomi impliciti generati da Postgres/SQLAlchemy.
- Ogni pipeline di import ha il logging abilitato: ogni entry point standalone chiama `marketmind_ai.logging_config.configure_logging()` all'avvio (mai i moduli di libreria, per non duplicare handler). Convenzione completa su livelli/destinazione in `Market Mind AI - Docs/Architettura/04_logging.md`.

## Fonti dati

Un file per fonte in `Market Mind AI - Docs/Data Providers/` (yfinance, GDELT, Finnhub, FRED, Financial Modeling Prep), ciascuno con endpoint usati, schema della risposta grezza, limiti dell'endpoint e interfaccia Pydantic di destinazione.

## Schema dati

Quattordici tabelle in quattro schema Postgres separati: `market_data` (`t_assets`, `t_market_prices` hypertable, `t_news_events`, `t_macro_events`, `t_company_events`), `decisions` (`t_model_runs`, `t_model_decisions`, `t_backtest_results`), `portfolio` (`t_portfolios`, `t_portfolio_positions`, `t_portfolio_snapshots`, `t_portfolio_position_snapshots` — tracking del portafoglio virtuale, vedi sotto; separato da `decisions` il 30-08-26: il portafoglio è lo stato che risulta dalle decisioni, non una decisione), `audit` (`t_ingestion_runs`, `t_audit_logs` — logging e auditing operativo). Nessuna delle tabelle di ingestion conosce strutturalmente le fonti dati: la provenienza vive solo in `source`/`fetched_at`, e `raw_payload` in JSONB dove serve preservare il payload originale (non su `t_market_prices`, che è già completamente tipizzata) — mai nella struttura delle colonne, così da poter sostituire o aggiungere fonti senza modificare lo schema. `t_audit_logs` è popolata da un trigger Postgres generico, non dallo strato applicativo. ER diagram completo, tipi, chiavi e indicizzazione in `Market Mind AI - Docs/Architettura/01_schema_dati_er.md`.

## Decisioni prese il 29-08-26 (revisione interattiva, dettaglio completo in `Market Mind AI - Docs/agenda.md`)

Motore di backtest: vectorbt confermato. Cadenza delle decisioni: settimanale su tutti i 500 asset in una prima fase, con trigger event-driven da valutare in futuro in base a costi e risultati osservati. Granularità temporale dei prezzi: intraday (oraria). Orizzonte storico di backfill: nessuno — si accumula in avanti da quando il sistema è online, su tutte le fonti. Entity linking GDELT → asset: match su nome azienda/ticker estratto dal `QUADGRAM` di Web NGrams. Dati macro FRED: API ALFRED (vintage), non le serie standard, per coerenza col principio no-look-ahead. Retention strategy: safeguard di un anno per ora, da rivedere in base a test futuri. `t_audit_logs` popolata da trigger Postgres, non dallo strato applicativo. `run_ts`/`decision_ts` normalizzati a `ts`; PK di `t_market_prices` estesa a `(asset_id, ts, source)`. Tracking del portafoglio virtuale: quattro nuove tabelle — `t_portfolios` (contenitore generico, `portfolio_type` model/benchmark, stato corrente mutabile cash/equity) e `t_portfolio_positions` (stato corrente mutabile per asset), più `t_portfolio_snapshots`/`t_portfolio_position_snapshots` (storico append-only alimentato da trigger Postgres, stesso pattern di `t_audit_logs` ma con colonne tipizzate); benchmark di confronto SPY buy & hold, modellato come un'altra riga di `t_portfolios` invece di una colonna dedicata.

## Decisioni prese il 30-08-26

Le quattro tabelle del tracking del portafoglio virtuale (agenda #33/#34) vivono in uno schema Postgres dedicato, `portfolio`, non in `decisions` come nella stesura del 29-08-26 (agenda #35): il portafoglio è lo stato applicativo che risulta dall'eseguire le decisioni nel tempo, un dominio diverso da `t_model_decisions`/`t_backtest_results`, che restano l'unico contenuto di `decisions`. Nessun cambio a tabelle/colonne/vincoli, solo allo schema Postgres che le contiene; l'unica FK che ora attraversa il confine tra `decisions` e `portfolio` è quella nullable verso `t_model_runs.run_id` da `t_portfolio_snapshots`/`t_portfolio_position_snapshots`. Migrazione Alembic `0002_portfolio_tracking` applicata e verificata contro l'istanza reale lo stesso giorno.

Networking Podman per le future pipeline di ingestion (agenda #36, dettaglio in `Market Mind AI - Docs/Architettura/05_networking.md`): rete Podman condivisa dedicata (`deploy/quadlet/marketmind.network`), a cui `marketmind-db` e i futuri container di ingestion si aggancerebbero per parlarsi via il DNS integrato di Podman (nome container, es. `marketmind-db:5432`) invece che tramite la porta pubblicata sull'host — che resta comunque invariata per gli strumenti che girano sull'host nudo. **Non ancora attiva**: il primo tentativo ha mandato `marketmind-db.service` in crash loop (`netavark: create bridge: Netlink error: Operation not supported`) — causa diagnosticata in `Market Mind AI - Docs/tasks/2026-08-30-networking-podman.md`: un kernel già aggiornato ma non ancora ricaricato sulla macchina di sviluppo, non un limite di design. Rollback immediato, DB di nuovo `healthy` sulla rete di default. Da riattivare dopo un riavvio (checklist in `deploy/quadlet/marketmind-db.container`).

## Decisioni ancora aperte — non assumere, segnalare l'ambiguità

Logica di windowing dell'Historical Context Builder e definizione delle skill/tool esposte all'LLM per estenderlo a runtime (richiesta di più storico, eventi di aziende correlate): impatta `llm/` e `decision_engine/`, richiede un provider con function calling/tool-use affidabile (agenda #10, #21). Automazione CI/CD del deploy container via GitHub Actions: runner self-hosted sul server di destinazione vs. runner GitHub-hosted con deploy via SSH, gestione dei secret nel workflow, eventuale build custom delle immagini — bloccata a monte dall'assenza di un server di destinazione diverso da `localhost` (agenda #22, #23, #24; dettaglio in `Market Mind AI - Docs/Architettura/02_ci_cd.md`). Tre punti concreti emersi dal primo deploy reale del 29-08-26 (agenda #25, #27, #28; dettaglio in `Market Mind AI - Docs/tasks/2026-08-29-deploy-db-podman.md`): networking hardcodato su `127.0.0.1` (il caso "più container sullo stesso host" è disegnato ma non ancora attivo, agenda #36 — resta aperto solo il caso di un server remoto), un solo ruolo Postgres superuser senza permessi differenziati per area applicativa (`Market Mind AI - Docs/Architettura/03_utenti_db.md`), e il `podman secret` della password DB rimasto disallineato dal ruolo reale — rischio concreto se il volume venisse mai ricreato da zero. Promemoria operativi non bloccanti: verificare se il reset dei 250 req/giorno del piano free FMP è giornaliero o su finestra mobile (agenda #16); vincolo di licenza "uso non commerciale" del piano free Finnhub, da monitorare se lo scope del progetto cambiasse (agenda #18). Un punto applicativo (non di schema) sul neonato tracking del portafoglio virtuale (agenda #31): come escludere SPY dalla lista dei ~500 titoli su cui gira il motore decisionale, dato che `t_assets` non distingue "asset di universo" da "asset di solo benchmark".

## Convenzioni di documentazione

I documenti `.md` di progetto (non il codice) seguono uno stile a prosa continua, con tabelle solo per contenuto genuinamente tabulare — evitare elenchi puntati esagerati quando si aggiorna `Market Mind AI - Docs/Market Mind AI.md` o i file in `Market Mind AI - Docs/Data Providers/`. Usare mermaid per i grafici.
