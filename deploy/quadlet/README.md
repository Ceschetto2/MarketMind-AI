Quadlet Podman rootless per l'ambiente di sviluppo locale, una
sottodirectory per pacchetto: `marketmind_db/` (database, volume, ruoli,
migrazioni), `marketmind_pipelines/` (le otto pipeline),
`marketmind_llm_decision_engine/` (il Decision Engine). La rete condivisa
`marketmind.network` resta qui; i `.timer` sono in `deploy/systemd/<pacchetto>/`,
le immagini in `deploy/images/`.

`marketmind-db.container` avvia Postgres+TimescaleDB (immagine
`timescale/timescaledb:latest-pg16`) su `127.0.0.1:5432`, con i dati su un
volume Podman dichiarato in `marketmind-db-data.volume`. Le credenziali non
vivono nel file quadlet: la password passa da `podman secret`, l'utente e il
nome del database sono `marketmind` (devono combaciare con `.env`, vedi
`.env.example` in root). `marketmind.network` (deciso e attivato il 30-08-26)
è la rete Podman condivisa a cui `marketmind-db` è agganciato: i futuri
container delle pipeline di ingestion lo raggiungeranno come
`marketmind-db:5432` via il DNS integrato di Podman, indipendentemente dalla
porta pubblicata sull'host. Dettaglio completo, la diagnosi di un primo
tentativo fallito (kernel non ancora riavviato sulla macchina di sviluppo,
non un limite di design) e la verifica end-to-end in
`Market Mind AI - Docs/Architettura/05_networking.md` e
`Market Mind AI - Docs/tasks/2026-08-30-networking-podman.md`.

Setup — `deploy.sh` automatizza immagini, symlink, secret, avvio del DB,
ruoli, migrazioni e timer, rieseguibile senza effetti collaterali:

    MARKETMIND_DB_PASSWORD=xxx deploy/quadlet/deploy.sh --build

Senza `MARKETMIND_DB_PASSWORD` in ambiente, se il secret non esiste ancora
lo script te lo chiede a mano (solo in un terminale interattivo); in CI o
in uno script non interattivo la variabile è obbligatoria. `deploy.sh
--restart` forza un riavvio del servizio anche se già attivo. Vedi i
commenti in testa allo script per le altre variabili d'ambiente supportate.

Le migrazioni non si lanciano più dall'host: `marketmind-migrate.container`
(immagine `marketmind-migrate`) esegue `alembic upgrade head` come one-shot
dopo l'avvio del DB, e `deploy.sh` la riesegue a ogni deploy prima di
abilitare i timer. Pipeline e Decision Engine la dichiarano in
`Wants=`/`After=`, quindi partono sempre su uno schema aggiornato. Per uno
sviluppo locale senza container resta possibile `uv run alembic upgrade head`
con `.env` valorizzato.

Questo target Quadlet è solo per sviluppo su localhost. `deploy.sh` è
scritto per restare riusabile anche da un futuro workflow GitHub Actions
quando esisterà un target di produzione (server NixOS con
`virtualisation.oci-containers`) — non ancora definito, vedi
`Market Mind AI - Docs/Architettura/02_ci_cd.md` e `agenda.md`.

## Pipeline di ingestion

Le otto pipeline di ingestion (`yfinance-prices`, `yfinance-assets`,
`gdelt-ngrams`, `finnhub-news`, `finnhub-earnings`, `fred`, `fmp`,
`universe-csv`) condividono l'immagine `marketmind-pipelines`
(`deploy/images/marketmind_pipelines.dockerfile`: solo il pacchetto
`marketmind_pipelines` e le sue dipendenze, installati come wheel da
`uv.lock` con `uv sync --frozen --package`). Ogni pacchetto ha la propria
immagine — `marketmind-migrate`, `marketmind-pipelines`,
`marketmind-llm-decision-engine`, `marketmind-frontend` (quest'ultima senza
ancora una unit Quadlet) —, costruite tutte da `deploy.sh --build`.
L'isolamento tra pipeline è a livello di container Quadlet, uno per
pipeline (stesso `Image=`, `Exec=` diverso).

Ogni `marketmind-ingest-<pipeline>.container` gira su
`Network=marketmind.network` per raggiungere il database come
`marketmind-db:5432` via il DNS integrato di Podman — non
`127.0.0.1`/la porta pubblicata sull'host, che sono per due container
distinti sulla stessa rete, non lo stesso host network. A differenza di
`marketmind-db.container`, è `Type=oneshot` in `[Service]`: si avvia,
esegue il proprio `Exec=run <pipeline>` (argomenti dell'entry point
`python -m marketmind_pipelines` dell'immagine) una volta ed esce — nessun loop/scheduler interno al container. La password
del ruolo Postgres applicativo dedicato all'ingestion, `marketmind_ingestion`,
arriva da `podman secret marketmind-ingestion-password`; le altre variabili
di connessione (`POSTGRES_HOST=marketmind-db`, `POSTGRES_PORT`,
`POSTGRES_DB`, `POSTGRES_USER=marketmind_ingestion`) sono in chiaro via
`Environment=`, coerenti con `marketmind-db.container`. `finnhub-news`,
`finnhub-earnings`, `fred` e `fmp` montano anche il rispettivo
`podman secret marketmind-<fonte>-api-key`.

**Il battito viene da un `.timer`, non da `deploy/quadlet/`**: le unit
`.timer` (`marketmind-ingest-<pipeline>.timer`, una per pipeline tranne
`fmp`, che non ne ha uno proprio — vedi sotto) vivono in
`deploy/systemd/<pacchetto>/`, non qui — il generatore Quadlet che scansiona
questa directory capisce solo `.container`/`.network`/`.volume`, un
`.timer` piazzato qui non verrebbe mai processato. `deploy.sh` li linka
in `~/.config/systemd/user/` (non `~/.config/containers/systemd/`) e li
abilita esplicitamente (`systemctl --user enable --now`, a differenza
delle unit Quadlet, abilitate implicitamente dal generatore). In
alternativa al timer, ogni pipeline è triggerabile a mano con
`deploy.sh --trigger <pipeline>`.

`fmp` è l'eccezione: nessun `.timer` proprio — `marketmind-ingest-finnhub-earnings.container`
ha `OnSuccess=marketmind-ingest-fmp.service` in `[Unit]`, quindi `fmp`
parte automaticamente al completamento con successo di
`finnhub-earnings` (vincolata dal budget di 250 richieste/giorno del
piano free FMP, dettaglio in
`Market Mind AI - Docs/pipelines/01_trigger_e_scheduling.md`).

Dettaglio completo di granularità pipeline/container, naming e cadenze in
`Market Mind AI - Docs/pipelines/00_container_e_immagini.md` e
`Market Mind AI - Docs/pipelines/01_trigger_e_scheduling.md`.
