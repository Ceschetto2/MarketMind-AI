Quadlet Podman rootless per l'ambiente di sviluppo locale.

`marketmind-db.container` avvia Postgres+TimescaleDB (immagine
`timescale/timescaledb:latest-pg16`) su `127.0.0.1:5432`, con i dati su un
volume Podman dichiarato in `marketmind-db-data.volume`. Le credenziali non
vivono nel file quadlet: la password passa da `podman secret`, l'utente e il
nome del database sono `marketmind` (devono combaciare con `.env`, vedi
`.env.example` in root).

Setup:

    mkdir -p ~/.config/containers/systemd
    ln -s $(pwd)/deploy/quadlet/marketmind-db.container ~/.config/containers/systemd/
    ln -s $(pwd)/deploy/quadlet/marketmind-db-data.volume ~/.config/containers/systemd/
    podman secret create marketmind-db-password -   # incolla la password e Ctrl-D
    systemctl --user daemon-reload
    systemctl --user start marketmind-db.service

Poi, con `.env` valorizzato in root del progetto:

    uv run alembic upgrade head

Questo target Quadlet è solo per sviluppo su localhost. Il target di
produzione (server NixOS con `virtualisation.oci-containers`) non è ancora
definito.
