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

Onboarding e test delle 5 API dati completati (chiavi salvate localmente — **mai committarle**, vanno in `podman secret` o in un `.env` escluso da git). `experiments/` contiene script usa-e-getta per scaricare campioni grezzi da ciascuna fonte (output in `data/samples/`, escluso da git), usati per validare i campi osservati nei doc di onboarding. Lo schema dati (ER diagram, naming conventions, indicizzazione) è formalizzato in `Market Mind AI - Docs/db/01_schema_dati_er.md`; la struttura delle cartelle in `Market Mind AI - Docs/Architettura/00_struttura_cartelle.md`.

Il modulo `db/` (modelli SQLAlchemy, naming convention espliciti, session factory) è implementato, allineato all'ER diagram, con la config di Alembic in `pyproject.toml` (niente `alembic.ini`). Quattro migrazioni sono scritte, applicate e verificate contro l'istanza Postgres+TimescaleDB reale di sviluppo (`deploy/quadlet/deploy.sh`, deploy idempotente e non interattivo se `MARKETMIND_DB_PASSWORD` è in ambiente): `0001_initial_schema` (schema `market_data`/`decisions`/`audit`, hypertable prezzi, retention policy, trigger di audit generico); `0002_portfolio_tracking` (schema `portfolio`, quattro tabelle di tracking del portafoglio virtuale con due trigger dedicati); `0003_raw_payload_split` (schema `raw`, payload grezzo di `t_news_events`/`t_company_events` separato dalle tabelle raffinate); `0004_universe_membership` (`t_universe_members` in `market_data`, universo osservato distinto dal sottoinsieme su cui gira il motore decisionale). Il logging condiviso (`marketmind_ai.logging_config.configure_logging()`) esiste ed è in uso da `alembic/env.py`.

Non ancora implementati: `schemas/` (le 6 interfacce Pydantic, nessuna ancora scritta in codice, solo documentata), `ingestion/`, `llm/`, `decision_engine/`, `backtest/`, `dashboard/`, `orchestration/`; lo script SQL per i due ruoli Postgres applicativi (`marketmind_ingestion`/`marketmind_app`, dettaglio in `Market Mind AI - Docs/db/03_utenti_db.md`) e il suo wiring in `deploy.sh`; l'architettura delle otto pipeline di ingestion (`Market Mind AI - Docs/pipelines/`); il CSV di seed dell'universo (`seeds/universe.csv`).

## Stack

- **Linguaggio**: Python
- **Database**: PostgreSQL + TimescaleDB — tabelle normalizzate, niente pipeline di riprocessamento bronze/silver a più stadi (raffinata e payload grezzo restano scritti nella stessa transazione dallo stesso script di ingestion); il payload grezzo, dove preservato, vive in una tabella dedicata di uno schema separato (`raw`), non inline sulla tabella raffinata
- **Validazione dati**: Pydantic
- **ORM/migrazioni**: SQLAlchemy + Alembic
- **Backtesting**: vectorbt, con `cash_sharing=True` per simulare un unico portafoglio
- **Dashboard**: Streamlit, connessa direttamente a Postgres via SQLAlchemy
- **Orchestrazione**: nessun tool dedicato — script Python + timer systemd
- **Container**: Podman rootless via systemd Quadlet, deploy automatizzato da `deploy/quadlet/deploy.sh`; target futuro: server NixOS con `virtualisation.oci-containers`. Per il momento sviluppo localhost. Rete Podman condivisa `marketmind.network` (`Market Mind AI - Docs/Architettura/05_networking.md`) attiva e verificata: `marketmind-db` la usa già, i futuri container di ingestion la raggiungeranno come `marketmind-db:5432` via il DNS integrato di Podman. Direzione CI/CD (GitHub Actions) ancora aperta, vedi `Market Mind AI - Docs/Architettura/02_ci_cd.md`.
- **Provider LLM**: Gemini per la prima implementazione, dietro un'interfaccia generica (`llm/base.py`); nuovi provider si aggiungono come moduli affiancati senza toccare `decision_engine/`.

## Regole architetturali da rispettare

- Ogni script di ingestion deve produrre output conforme a una delle interfacce Pydantic in `Market Mind AI - Docs/Data Providers/00_schema_interfacce.md` (`AssetRecord`, `MarketPriceRecord`, `NewsEventRecord`, `MacroEventRecord`, `CompanyEventRecord`, `UniverseMemberRecord`) prima di scrivere su Postgres — mai il payload grezzo direttamente.
- Le interfacce di ingestion usano `symbol`/`indicator` come identificatore naturale, non `asset_id`: la risoluzione all'id interno avviene nello strato di scrittura/upsert in `db/`, non negli script di ingestion.
- GDELT: usare il dataset Web NGrams per la copertura ampia sui 500 asset, non la DOC API — il rate limit di 1 richiesta/5s la rende impraticabile a quella scala. L'entity linking articolo → asset si basa su un match testuale tra nome azienda/ticker e il `QUADGRAM` estratto dal flusso Web NGrams, nessun servizio NLP dedicato. Dettagli in `Market Mind AI - Docs/Data Providers/02_gdelt_onboarding.md`.
- Dati macro FRED: si usano i valori vintage via l'API ALFRED (`realtime_start`/`realtime_end` impostati al momento della query), non le serie standard riviste nel tempo — necessario per coerenza col principio no-look-ahead del progetto.
- Cadenza e storico: il motore decisionale genera decisioni settimanali su tutti gli asset dell'universo (un trigger event-driven resta una direzione futura, da valutare in base a costi/risultati osservati sulla cadenza settimanale); i prezzi si accumulano a granularità intraday (oraria); nessuna fonte prevede un backfill storico profondo — l'ingestion parte da subito e accumula i dati in avanti.
- Retention: safeguard di un anno su tutte le tabelle soggette a rotazione, da rivedere in base a test futuri e alla mole dati reale.
- Naming conventions di schema (nomi tabella/colonna/vincolo/indice) sono fissate in `Market Mind AI - Docs/db/01_schema_dati_er.md` § Naming conventions — in particolare la `naming_convention` esplicita su `MetaData` (`pk_<table>`, `fk_<table>_<column>_<ref_table>`, `uq_<table>_<column>`, `ck_<table>_<constraint_name>`) e il prefisso `i<access_method>_` per gli indici creati esplicitamente. Non introdurre nomi impliciti generati da Postgres/SQLAlchemy.
- Ogni pipeline di import ha il logging abilitato: ogni entry point standalone chiama `marketmind_ai.logging_config.configure_logging()` all'avvio (mai i moduli di libreria, per non duplicare handler). Convenzione completa su livelli/destinazione in `Market Mind AI - Docs/Architettura/04_logging.md`.

## Fonti dati

Un file per fonte in `Market Mind AI - Docs/Data Providers/` (yfinance, GDELT, Finnhub, FRED, Financial Modeling Prep), ciascuno con endpoint usati, schema della risposta grezza, limiti dell'endpoint e interfaccia Pydantic di destinazione.

## Schema dati

Diciassette tabelle in cinque schema Postgres separati: `market_data` (`t_assets`, `t_market_prices` hypertable, `t_news_events`, `t_macro_events`, `t_company_events`, `t_universe_members` — universo osservato, ~500 titoli + benchmark SPY, distinto dal sottoinsieme su cui gira davvero il motore decisionale via `is_benchmark`), `raw` (`t_news_events_raw`, `t_company_events_raw` — payload grezzo separato dalle rispettive tabelle raffinate, relazione 1:1 con FK `ON DELETE CASCADE`), `decisions` (`t_model_runs`, `t_model_decisions`, `t_backtest_results`), `portfolio` (`t_portfolios`, `t_portfolio_positions`, `t_portfolio_snapshots`, `t_portfolio_position_snapshots` — tracking del portafoglio virtuale; separato da `decisions` perché il portafoglio è lo stato che risulta dalle decisioni, non una decisione), `audit` (`t_ingestion_runs`, `t_audit_logs` — logging e auditing operativo). Nessuna delle tabelle di ingestion conosce strutturalmente le fonti dati: la provenienza vive solo in `source`/`fetched_at`, mai nella struttura delle colonne, così da poter sostituire o aggiungere fonti senza modificare lo schema. `t_audit_logs` è popolata da un trigger Postgres generico, non dallo strato applicativo — `t_universe_members`, mutabile e di ingestion come le altre cinque di `market_data`, rientra in questo meccanismo generico (a differenza di `raw`/`portfolio`, escluse per motivi diversi: insert-only le prime, per evitare doppio logging le seconde). Lo schema `raw` è separato per generalizzare il pattern a future tabelle di payload grezzo, non per permessi differenziati realmente in vigore oggi: `marketmind_ingestion` ha lo stesso `GRANT` su `market_data` e `raw`. ER diagram completo, tipi, chiavi e indicizzazione in `Market Mind AI - Docs/db/01_schema_dati_er.md`.

## Ambiguità aperte — non assumere, segnalare

Logica di windowing dell'Historical Context Builder e definizione delle skill/tool esposte all'LLM per estenderlo a runtime (richiesta di più storico, eventi di aziende correlate): impatta `llm/` e `decision_engine/`, richiede un provider con function calling/tool-use affidabile. Automazione CI/CD del deploy container via GitHub Actions: runner self-hosted sul server di destinazione vs. runner GitHub-hosted con deploy via SSH, gestione dei secret nel workflow, eventuale build custom delle immagini — bloccata a monte dall'assenza di un server di destinazione diverso da `localhost` (dettaglio in `Market Mind AI - Docs/Architettura/02_ci_cd.md`). Networking: il caso "più container sullo stesso host" è risolto — resta aperto solo il caso di un server remoto. Ruolo di `orchestration/`: non più chiaramente coerente con l'architettura delle pipeline di ingestion, che invocano il proprio entry point direttamente da Quadlet senza passare da lì — da chiarire se sopravviva solo per i cicli futuri di `decision_engine/`/`backtest/`. Drift minore da correggere: `t_ingestion_runs.source` nell'ER diagram elenca anche `gdelt-ngrams`/`gdelt-doc` come fonti attese, ma il `CHECK` in vigore ammette solo il generico `gdelt`. Promemoria operativi non bloccanti: verificare se il reset dei 250 req/giorno del piano free FMP è giornaliero o su finestra mobile; vincolo di licenza "uso non commerciale" del piano free Finnhub, da monitorare se lo scope del progetto cambiasse.

## Convenzioni di documentazione

I documenti `.md` di progetto (non il codice) seguono uno stile a prosa continua, con tabelle solo per contenuto genuinamente tabulare — evitare elenchi puntati esagerati quando si aggiorna `Market Mind AI - Docs/Market Mind AI.md` o i file in `Market Mind AI - Docs/Data Providers/`. Usare mermaid per i grafici.

I cambiamenti si registrano in `Market Mind AI - Docs/agenda.md` (data, ragionamento, alternative scartate) — mai come narrazione dentro un documento. Ogni altro documento, `CLAUDE.md` incluso, riporta solo lo stato corrente del progetto: niente "deciso il...", niente riferimenti puntuali a voci di agenda, niente racconto di come si è arrivati a una scelta — solo la scelta stessa e, se genuinamente irrisolta, il fatto che lo sia. Le uniche eccezioni sono i file sotto `Market Mind AI - Docs/tasks/`, che per natura documentano un intervento specifico nel tempo (diagnosi, tentativi, esito) e possono quindi avere una narrazione con una propria evoluzione — un prima/dopo o uno stato ancora da chiudere — che altrove andrebbe invece rimossa.