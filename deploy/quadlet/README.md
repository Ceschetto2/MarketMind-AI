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
