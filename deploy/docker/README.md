# Deploy sul server UniBa (Docker)

Deploy di MarketMind-AI sul server di laboratorio UniBa (Ubuntu 20.04, Docker rootful, nessun `sudo`), guidato dal laptop via SSH con `deploy/docker/deploy.sh`. Le unit Quadlet in `deploy/quadlet/` e `deploy/systemd/` restano il deploy locale del laptop.

Sul server non vive nulla nella home dell'utente: è un disco NTFS con permessi `777` per tutti gli utenti, quindi qualunque file lì sarebbe leggibile e modificabile da chiunque. Ne segue il disegno:

| Cosa | Dove |
|---|---|
| Immagini | costruite in locale con podman (tag `:uniba`), caricate con `podman save \| ssh docker load`, `:latest` sul server |
| Dati del DB | volume Docker `marketmind-db-data` |
| Secret | volume Docker `marketmind-secrets`, un file per secret, montato in sola lettura solo nei container che lo usano e letto dal codice tramite `<VARIABILE>_FILE` |
| Backup | volume Docker `marketmind-backups`, `pg_dump` giornaliero alle 3:30, 14 giorni di rotazione; `--fetch-backup` ne copia l'ultimo sul laptop |
| Schedulazione | crontab dell'utente (`/var/spool/cron`), blocco delimitato da marcatori e riscritto a ogni deploy; stessi orari dei `.timer` di `deploy/systemd/` |
| Rete | rete Docker `marketmind`, il DB risponde come `marketmind-db:5432` |

Il DB è pubblicato solo su `127.0.0.1:5432` del server. Dal laptop si raggiunge con un tunnel, su una porta locale diversa da quella del Postgres di sviluppo:

```
ssh -N -L 15432:127.0.0.1:5432 marketmind
```

Ogni job gira in un container con nome fisso (`marketmind-ingest-<pipeline>`, `marketmind-decide`): il nome fa da mutex contro run sovrapposti, e il container resta fino al run successivo, così `docker logs <nome>` mostra l'output dell'ultimo giro. Lo storico degli esiti è in `audit.t_ingestion_runs` e `decisions.t_model_runs`.

## Prerequisiti sul laptop

L'alias SSH `marketmind` in `~/.ssh/config`, con `ControlMaster auto`/`ControlPersist`, e una connessione master aperta (l'accesso al server è solo a password). Password e chiavi API del server in `~/.config/marketmind/server.env` (`600`, mai nel repo): `MARKETMIND_DB_PASSWORD`, `MARKETMIND_INGESTION_PASSWORD`, `MARKETMIND_APP_PASSWORD`, `MARKETMIND_READONLY_PASSWORD` (ruolo di sola lettura della dashboard), `FINNHUB_API_KEY`, `FRED_API_KEY`, `FMP_API_KEY`, `GEMINI_API_KEY`.

## Comandi

```
deploy/docker/deploy.sh --build        # deploy completo con immagini ricostruite
deploy/docker/deploy.sh                # deploy completo (secret, DB, ruoli, migrazioni, crontab)
deploy/docker/deploy.sh --trigger fred # un job subito, in primo piano
deploy/docker/deploy.sh --status       # crontab e container del progetto
deploy/docker/deploy.sh --remove-cron  # ferma la schedulazione
deploy/docker/compare-db.sh            # confronto laptop/server dopo un restore
```

Le opzioni complete sono in testa a `deploy.sh`. Il restore di un dump su un server nuovo passa da `--db-only` (ruoli creati, schema vuoto, nessuna migrazione), poi `pg_restore` tra `timescaledb_pre_restore()` e `timescaledb_post_restore()`, poi un deploy con `--no-cron` per i grant e la verifica con `compare-db.sh`, infine un deploy normale.
