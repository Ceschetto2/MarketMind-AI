Quadlet Podman rootless per l'ambiente di sviluppo locale.

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

Setup — `deploy.sh` automatizza symlink, secret e avvio del servizio,
rieseguibile senza effetti collaterali:

    MARKETMIND_DB_PASSWORD=xxx deploy/quadlet/deploy.sh

Senza `MARKETMIND_DB_PASSWORD` in ambiente, se il secret non esiste ancora
lo script te lo chiede a mano (solo in un terminale interattivo); in CI o
in uno script non interattivo la variabile è obbligatoria. `deploy.sh
--restart` forza un riavvio del servizio anche se già attivo. Vedi i
commenti in testa allo script per le altre variabili d'ambiente supportate.

Poi, con `.env` valorizzato in root del progetto:

    uv run alembic upgrade head

Questo target Quadlet è solo per sviluppo su localhost. `deploy.sh` è
scritto per restare riusabile anche da un futuro workflow GitHub Actions
quando esisterà un target di produzione (server NixOS con
`virtualisation.oci-containers`) — non ancora definito, vedi
`Market Mind AI - Docs/Architettura/02_ci_cd.md` e `agenda.md`.

## Pipeline di ingestion

Le otto pipeline di ingestion (`yfinance-prices`, `yfinance-assets`,
`gdelt-ngrams`, `finnhub-news`, `finnhub-earnings`, `fred`, `fmp`,
`universe-csv`) condividono un'unica immagine Podman,
`marketmind-ingestion`, costruita da `deploy/ingestion/Containerfile`
(pacchetto `marketmind_ai` installato via `uv sync --frozen` da `uv.lock`,
niente `pip install` a mano) — coerente con
`Market Mind AI - Docs/pipelines/00_container_e_immagini.md`. L'isolamento
tra pipeline è a livello di container Quadlet, uno per pipeline (stesso
`Image=`, `Exec=` diverso), non di immagine:

    podman build -t marketmind-ingestion:latest -f deploy/ingestion/Containerfile .

Ogni `marketmind-ingest-<pipeline>.container` gira su
`Network=marketmind.network` per raggiungere il database come
`marketmind-db:5432` via il DNS integrato di Podman — non
`127.0.0.1`/la porta pubblicata sull'host, che sono per due container
distinti sulla stessa rete, non lo stesso host network. A differenza di
`marketmind-db.container`, è `Type=oneshot` in `[Service]`: si avvia,
esegue il proprio `Exec=python -m marketmind_ai.ingestion.<pipeline>_pipeline`
una volta ed esce — nessun loop/scheduler interno al container. La password
del ruolo Postgres applicativo dedicato all'ingestion, `marketmind_ingestion`,
arriva da `podman secret marketmind-ingestion-password`; le altre variabili
di connessione (`POSTGRES_HOST=marketmind-db`, `POSTGRES_PORT`,
`POSTGRES_DB`, `POSTGRES_USER=marketmind_ingestion`) sono in chiaro via
`Environment=`, coerenti con `marketmind-db.container`. `finnhub-news`,
`finnhub-earnings`, `fred` e `fmp` montano anche il rispettivo
`podman secret marketmind-<fonte>-api-key`.

**Il battito viene da un `.timer`, non da `deploy/quadlet/`**: le unit
`.timer` (`marketmind-ingest-<pipeline>.timer`, una per pipeline tranne
`fmp`, che non ne ha uno proprio — vedi sotto) vivono in `systemd/` a
livello di root del repo, non qui — il generatore Quadlet che scansiona
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
