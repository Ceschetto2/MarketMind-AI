#!/usr/bin/env bash
# Deploy di MarketMind-AI sul server UniBa (Ubuntu 20.04 + Docker), guidato
# dal laptop via SSH: sul server non vive né un checkout del repo né uno
# script. La home dell'utente sul server è un disco NTFS con permessi 777 per
# tutti gli utenti, quindi nulla di eseguibile o segreto può stare lì: le
# immagini si costruiscono qui con podman e si caricano con `docker load`, i
# dati del DB, i secret e i backup vivono in volumi Docker (data root ext4,
# accessibile solo a root/gruppo docker), la schedulazione è la crontab
# dell'utente (in /var/spool/cron, non nella home), che lancia `docker run`
# senza riferimenti a file sul disco condiviso. Disegno completo in
# `Market Mind AI - Docs/tasks/2026-10-10-uniba-server-docker.md`.
#
# Idempotente come deploy/quadlet/deploy.sh: rilanciarlo riallinea secret,
# password dei ruoli, migrazioni e crontab al valore corrente.
#
# Uso:
#   deploy/docker/deploy.sh                    # rete, volumi, secret, DB, ruoli, migrazioni, crontab
#   deploy/docker/deploy.sh --build            # come sopra, ricostruendo e caricando prima le immagini
#   deploy/docker/deploy.sh --db-only          # solo DB e ruoli (senza grant sugli schema), niente
#                                              #   migrazioni né crontab: prepara il restore di un dump
#   deploy/docker/deploy.sh --no-cron          # tutto tranne l'installazione della crontab
#   deploy/docker/deploy.sh --keep-secrets     # non riscrive i secret né le password dei ruoli
#   deploy/docker/deploy.sh --recreate-db      # ricrea il container DB (es. nuova immagine); i dati
#                                              #   restano nel volume
#   (le opzioni sopra si possono combinare)
#   deploy/docker/deploy.sh --trigger <job>    # esegue subito un job in primo piano ed esce
#   deploy/docker/deploy.sh --print-cron       # stampa le righe di crontab generate, senza toccare il server
#   deploy/docker/deploy.sh --remove-cron      # rimuove le righe del progetto dalla crontab ed esce
#   deploy/docker/deploy.sh --status           # crontab e container del progetto, poi esce
#   deploy/docker/deploy.sh --fetch-backup     # copia sul laptop l'ultimo backup del DB ed esce
#
# Job: gdelt-ngrams, finnhub-news, yfinance-prices, finnhub-earnings, fmp,
# fred, universe-csv, yfinance-assets, decide, migrate.
#
# Variabili d'ambiente:
#   MARKETMIND_SSH_HOST     alias SSH del server (default: marketmind)
#   MARKETMIND_ENV_FILE     file con password e chiavi API (default:
#                           ~/.config/marketmind/server.env): MARKETMIND_DB_PASSWORD,
#                           MARKETMIND_INGESTION_PASSWORD, MARKETMIND_APP_PASSWORD,
#                           MARKETMIND_READONLY_PASSWORD (ruolo della dashboard, che
#                           gira sul laptop via tunnel: nessun secret nel volume),
#                           FINNHUB_API_KEY, FRED_API_KEY, FMP_API_KEY, GEMINI_API_KEY
#   MARKETMIND_DB_PORT      porta pubblicata su 127.0.0.1 del server (default: 5432)
#   MARKETMIND_DB_MEMORY    limite di memoria del container DB (default: 4g)
#   MARKETMIND_BACKUP_DIR   destinazione di --fetch-backup (default: ~/marketmind-backups)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGES_SRC="$REPO_ROOT/deploy/images"
ROLES_SQL_TEMPLATE="$REPO_ROOT/deploy/quadlet/marketmind_db/init-roles.sql.tmpl"

SSH_HOST="${MARKETMIND_SSH_HOST:-marketmind}"
ENV_FILE="${MARKETMIND_ENV_FILE:-$HOME/.config/marketmind/server.env}"
DB_PORT="${MARKETMIND_DB_PORT:-5432}"
DB_MEMORY="${MARKETMIND_DB_MEMORY:-4g}"
BACKUP_DIR="${MARKETMIND_BACKUP_DIR:-$HOME/marketmind-backups}"

# Stessa versione di TimescaleDB del DB di sviluppo: un restore richiede la
# stessa versione dell'estensione da entrambi i lati, un tag mobile no.
DB_IMAGE="docker.io/timescale/timescaledb:2.29.2-pg16"
NETWORK="marketmind"
DB_CONTAINER="marketmind-db"
DB_VOLUME="marketmind-db-data"
SECRETS_VOLUME="marketmind-secrets"
BACKUP_VOLUME="marketmind-backups"
BACKUP_RETENTION_DAYS=14

MIGRATE_IMAGE="localhost/marketmind-migrate:latest"
PIPELINES_IMAGE="localhost/marketmind-pipelines:latest"
DECIDE_IMAGE="localhost/marketmind-llm-decision-engine:latest"
# immagine -> dockerfile (deploy/images/). Il frontend non gira sul server.
IMAGES=(
    "marketmind-migrate:marketmind_db.dockerfile"
    "marketmind-pipelines:marketmind_pipelines.dockerfile"
    "marketmind-llm-decision-engine:marketmind_llm_decision_engine.dockerfile"
)

# secret (directory nel volume) -> variabile d'ambiente da cui si scrive
SECRETS=(
    "db-password:MARKETMIND_DB_PASSWORD"
    "ingestion-password:MARKETMIND_INGESTION_PASSWORD"
    "app-password:MARKETMIND_APP_PASSWORD"
    "finnhub-api-key:FINNHUB_API_KEY"
    "fred-api-key:FRED_API_KEY"
    "fmp-api-key:FMP_API_KEY"
    "gemini-api-key:GEMINI_API_KEY"
)

CRON_BEGIN="# >>> marketmind (deploy/docker/deploy.sh) >>>"
CRON_END="# <<< marketmind <<<"

log() { printf '[deploy] %s\n' "$*" >&2; }
fail() { log "ERRORE: $*"; exit 1; }
remote() { ssh "$SSH_HOST" "$@"; }

# --- Job: un container per run, nome fisso ---------------------------------
# Il nome fisso fa da mutex: se il run precedente è ancora in corso,
# `docker rm` (senza -f) fallisce e `docker run` trova il nome occupato, quindi
# il nuovo run viene saltato invece di sovrapporsi. Il container di un run
# concluso resta fino al run successivo, così `docker logs <nome>` mostra
# l'output dell'ultimo giro; lo storico degli esiti è in
# audit.t_ingestion_runs / decisions.t_model_runs.


# secret_mount <secret> <VARIABILE>: il solo secret montato in sola lettura,
# letto dal codice tramite <VARIABILE>_FILE (marketmind_common.config).
secret_mount() {
    printf -- '-e %s_FILE=/run/secrets/%s/value --mount type=volume,src=%s,dst=/run/secrets/%s,volume-subpath=%s,readonly' \
        "$2" "$1" "$SECRETS_VOLUME" "$1" "$1"
}

# frag <id>: i pezzi di cui è fatto un `docker run`. Nella crontab diventano
# variabili MM_<id> dichiarate in testa al blocco, perché cron tronca i
# comandi oltre ~1000 caratteri e una riga scritta per esteso li supera.
FRAGS=(RUN ADMIN INGEST APP FINNHUB FRED FMP GEMINI)
frag() {
    case "$1" in
        RUN) printf 'docker run --network %s --log-opt max-size=10m --log-opt max-file=2 -e POSTGRES_HOST=%s -e POSTGRES_PORT=5432 -e POSTGRES_DB=marketmind' \
                "$NETWORK" "$DB_CONTAINER" ;;
        ADMIN) printf -- '-e POSTGRES_USER=marketmind %s' "$(secret_mount db-password POSTGRES_PASSWORD)" ;;
        INGEST) printf -- '-e POSTGRES_USER=marketmind_ingestion %s' "$(secret_mount ingestion-password POSTGRES_PASSWORD)" ;;
        APP) printf -- '-e POSTGRES_USER=marketmind_app %s' "$(secret_mount app-password POSTGRES_PASSWORD)" ;;
        FINNHUB) secret_mount finnhub-api-key FINNHUB_API_KEY ;;
        FRED) secret_mount fred-api-key FRED_API_KEY ;;
        FMP) secret_mount fmp-api-key FMP_API_KEY ;;
        GEMINI) secret_mount gemini-api-key GEMINI_API_KEY ;;
    esac
}

# job_spec <job>: imposta J_NAME, J_IMAGE, J_ROLE, J_KEY (vuota se il job
# non usa chiavi API) e J_ARGS.
job_spec() {
    J_KEY=""
    case "$1" in
        migrate) J_NAME=marketmind-migrate; J_IMAGE=$MIGRATE_IMAGE; J_ROLE=ADMIN; J_ARGS="upgrade head" ;;
        decide) J_NAME=marketmind-decide; J_IMAGE=$DECIDE_IMAGE; J_ROLE=APP; J_KEY=GEMINI; J_ARGS="run-due" ;;
        finnhub-news|finnhub-earnings) J_KEY=FINNHUB ;;&
        fred) J_KEY=FRED ;;&
        fmp) J_KEY=FMP ;;&
        finnhub-news|finnhub-earnings|fred|fmp|gdelt-ngrams|universe-csv|yfinance-assets|yfinance-prices)
            J_NAME="marketmind-ingest-$1"; J_IMAGE=$PIPELINES_IMAGE; J_ROLE=INGEST; J_ARGS="run $1" ;;
        *) fail "job sconosciuto: $1" ;;
    esac
}

# job_line <job>: rimozione del container del run precedente + `docker run`,
# per esteso (per ssh, nessun limite di lunghezza).
job_line() {
    job_spec "$1"
    local key=""
    [ -n "$J_KEY" ] && key=" $(frag "$J_KEY")"
    printf 'docker rm %s >/dev/null 2>&1; %s --name %s %s%s %s %s' \
        "$J_NAME" "$(frag RUN)" "$J_NAME" "$(frag "$J_ROLE")" "$key" "$J_IMAGE" "$J_ARGS"
}

# cron_job <job>: come job_line ma sulle variabili MM_* della crontab.
cron_job() {
    job_spec "$1"
    local key=""
    [ -n "$J_KEY" ] && key=" \$MM_$J_KEY"
    printf '(docker rm %s >/dev/null 2>&1; $MM_RUN --name %s $MM_%s%s %s %s) >/dev/null 2>&1' \
        "$J_NAME" "$J_NAME" "$J_ROLE" "$key" "$J_IMAGE" "$J_ARGS"
}

# Righe di crontab del progetto. Orari in ora locale del server (Europe/Rome),
# gli stessi dei .timer systemd di deploy/systemd/. `fmp` non ha un orario
# proprio: parte solo se `finnhub-earnings` termina con exit 0 (success o
# partial), come l'`OnSuccess=` della unit Quadlet. In crontab `%` è un
# carattere speciale, da qui il `\%` nel comando di backup. Il blocco va
# sempre in fondo alla crontab: le sue variabili MM_* valgono per le righe
# che seguono, cioè solo le sue.
crontab_block() {
    echo "$CRON_BEGIN"
    echo "# Generato da deploy/docker/deploy.sh: non modificare a mano, viene riscritto a ogni deploy."
    local id
    for id in "${FRAGS[@]}"; do
        echo "MM_$id=$(frag "$id")"
    done
    echo "*/15 * * * * $(cron_job gdelt-ngrams)"
    echo "0 * * * * $(cron_job finnhub-news)"
    echo "0 * * * * $(cron_job yfinance-prices)"
    echo "0 * * * * $(cron_job decide)"
    echo "0 0 * * * $(cron_job finnhub-earnings) && $(cron_job fmp)"
    echo "0 0 * * * $(cron_job fred)"
    echo "0 0 1 * * $(cron_job universe-csv)"
    echo "0 0 1 * * $(cron_job yfinance-assets)"
    echo "30 3 * * * docker exec $DB_CONTAINER sh -c 'pg_dump -U marketmind -d marketmind -Fc -f /backups/marketmind-\$(date +\\%Y\\%m\\%d).dump && find /backups -name \"marketmind-*.dump\" -mtime +$((BACKUP_RETENTION_DAYS - 1)) -delete' >/dev/null 2>&1"
    echo "$CRON_END"
}


# crontab corrente senza il blocco del progetto (le altre righe restano)
current_crontab_without_block() {
    remote "crontab -l 2>/dev/null" | awk -v b="$CRON_BEGIN" -v e="$CRON_END" \
        '$0 == b { skip = 1; next } $0 == e { skip = 0; next } !skip' || true
}

# --- Comandi che escono subito ----------------------------------------------
case "${1:-}" in
    --trigger)
        job="${2:-}"
        [ -n "$job" ] || fail "--trigger richiede un job, es.: --trigger yfinance-prices"
        log "eseguo $job sul server (primo piano)"
        remote "$(job_line "$job")"
        exit 0
        ;;
    --print-cron)
        crontab_block
        exit 0
        ;;
    --remove-cron)
        log "rimuovo le righe del progetto dalla crontab di $SSH_HOST"
        current_crontab_without_block | remote "crontab -"
        log "crontab aggiornata"
        exit 0
        ;;
    --status)
        remote "crontab -l 2>/dev/null" | awk -v b="$CRON_BEGIN" -v e="$CRON_END" \
            '$0 == b { show = 1 } show { print } $0 == e { show = 0 }'
        remote "docker ps -a --filter name=marketmind --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'"
        exit 0
        ;;
    --fetch-backup)
        mkdir -p "$BACKUP_DIR"
        latest="$(remote "docker exec $DB_CONTAINER sh -c 'ls -1t /backups/marketmind-*.dump 2>/dev/null | head -1'")"
        [ -n "$latest" ] || fail "nessun backup sul server"
        dst="$BACKUP_DIR/$(basename "$latest")"
        log "copio $latest -> $dst"
        remote "docker exec $DB_CONTAINER cat $latest" > "$dst.part"
        mv "$dst.part" "$dst"
        log "backup copiato ($(du -h "$dst" | cut -f1))"
        exit 0
        ;;
esac

BUILD=0
DB_ONLY=0
NO_CRON=0
KEEP_SECRETS=0
RECREATE_DB=0
for arg in "$@"; do
    case "$arg" in
        --build) BUILD=1 ;;
        --db-only) DB_ONLY=1 ;;
        --no-cron) NO_CRON=1 ;;
        --keep-secrets) KEEP_SECRETS=1 ;;
        --recreate-db) RECREATE_DB=1 ;;
        *) fail "argomento sconosciuto: $arg" ;;
    esac
done

if [ "$KEEP_SECRETS" = "0" ]; then
    [ -f "$ENV_FILE" ] || fail "manca $ENV_FILE (password e chiavi API del server)"
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
fi

remote true || fail "server $SSH_HOST non raggiungibile via SSH"

# --- Immagini: build locale con podman, caricate sul server -----------------
if [ "$BUILD" = "1" ]; then
    command -v podman >/dev/null 2>&1 || fail "podman non trovato in PATH"
    # In locale il tag è :uniba, non :latest, così la build non sostituisce le
    # immagini usate dalle unit Quadlet del laptop; sul server diventa :latest.
    for entry in "${IMAGES[@]}"; do
        image="localhost/${entry%%:*}:latest"
        local_image="localhost/${entry%%:*}:uniba"
        dockerfile="${entry##*:}"
        log "build $local_image ($dockerfile)"
        podman build -q -t "$local_image" -f "$IMAGES_SRC/$dockerfile" "$REPO_ROOT" >/dev/null \
            || fail "build di $local_image fallita"
        old_id="$(remote "docker image inspect -f '{{.Id}}' $image 2>/dev/null" || true)"
        log "carico $image sul server"
        podman save "$local_image" | gzip -1 | remote "gunzip | docker load -q" >/dev/null \
            || fail "caricamento di $image fallito"
        remote "docker tag $local_image $image && docker rmi $local_image >/dev/null" \
            || fail "tag di $image fallito"
        new_id="$(remote "docker image inspect -f '{{.Id}}' $image")"
        # L'immagine precedente resterebbe orfana: si rimuove insieme ai
        # container di run conclusi che la usano (mai quelli in esecuzione).
        if [ -n "$old_id" ] && [ "$old_id" != "$new_id" ]; then
            remote "docker ps -aq --filter ancestor=$old_id --filter status=exited | xargs -r docker rm >/dev/null; docker rmi $old_id >/dev/null 2>&1" || true
        fi
    done
fi

# --- Rete, volumi, immagine DB -----------------------------------------------
log "rete e volumi"
remote "docker network inspect $NETWORK >/dev/null 2>&1 || docker network create $NETWORK >/dev/null"
for volume in "$DB_VOLUME" "$SECRETS_VOLUME" "$BACKUP_VOLUME"; do
    remote "docker volume create $volume >/dev/null"
done
remote "docker image inspect $DB_IMAGE >/dev/null 2>&1 || docker pull -q $DB_IMAGE >/dev/null" \
    || fail "pull di $DB_IMAGE fallito"

# --- Secret: un file per secret nel volume, scritto via stdin ---------------
# Il valore passa solo dallo stdin di ssh: mai su disco, mai negli argomenti
# di un processo, mai nell'ambiente di un container.
if [ "$KEEP_SECRETS" = "1" ]; then
    log "--keep-secrets: verifico i secret esistenti"
    for entry in "${SECRETS[@]}"; do
        name="${entry%%:*}"
        remote "docker run --rm --network none -v $SECRETS_VOLUME:/s:ro --entrypoint test $DB_IMAGE -s /s/$name/value" \
            || fail "secret $name mancante: serve un deploy senza --keep-secrets"
    done
else
    log "scrivo i secret nel volume $SECRETS_VOLUME"
    for entry in "${SECRETS[@]}"; do
        name="${entry%%:*}"
        var="${entry##*:}"
        [ -n "${!var:-}" ] || fail "\$$var non impostata in $ENV_FILE"
        printf '%s' "${!var}" | remote "docker run --rm -i --network none -v $SECRETS_VOLUME:/s --entrypoint sh $DB_IMAGE -c 'mkdir -p /s/$name && chmod 0755 /s/$name && cat > /s/$name/value.tmp && chmod 0444 /s/$name/value.tmp && mv /s/$name/value.tmp /s/$name/value'" \
            || fail "scrittura del secret $name fallita"
        log "  $name"
    done
fi

# --- Container DB ------------------------------------------------------------
# Riavviato dal demone Docker (root) dopo un reboot: non serve il linger.
# TS_TUNE_*: timescaledb-tune, all'inizializzazione del cluster, dimensiona
# la memoria su questi valori invece che sull'intera RAM del server condiviso.
if [ "$RECREATE_DB" = "1" ]; then
    log "--recreate-db: rimuovo il container $DB_CONTAINER (i dati restano in $DB_VOLUME)"
    remote "docker rm -f $DB_CONTAINER >/dev/null 2>&1" || true
fi
if remote "docker container inspect $DB_CONTAINER >/dev/null 2>&1"; then
    log "avvio $DB_CONTAINER (no-op se già attivo)"
    remote "docker start $DB_CONTAINER >/dev/null"
else
    log "creo $DB_CONTAINER ($DB_IMAGE)"
    remote "docker run -d --name $DB_CONTAINER --network $NETWORK --restart unless-stopped \
        --memory $DB_MEMORY -e TS_TUNE_MEMORY=${DB_MEMORY%g}GB -e TS_TUNE_NUM_CPUS=4 \
        -e POSTGRES_USER=marketmind -e POSTGRES_DB=marketmind \
        $(secret_mount db-password POSTGRES_PASSWORD) \
        -v $DB_VOLUME:/var/lib/postgresql/data -v $BACKUP_VOLUME:/backups \
        -p 127.0.0.1:$DB_PORT:5432 \
        --health-cmd 'pg_isready -U marketmind -d marketmind' --health-interval 10s --health-retries 5 \
        --log-opt max-size=10m --log-opt max-file=3 \
        $DB_IMAGE >/dev/null"
fi

log "attendo che $DB_CONTAINER sia healthy..."
db_healthy=0
for _ in $(seq 1 60); do
    if [ "$(remote "docker inspect -f '{{.State.Health.Status}}' $DB_CONTAINER" 2>/dev/null)" = "healthy" ]; then
        db_healthy=1
        break
    fi
    sleep 2
done
[ "$db_healthy" = "1" ] || fail "$DB_CONTAINER non healthy entro 120s — controlla con: ssh $SSH_HOST docker logs $DB_CONTAINER"
log "$DB_CONTAINER healthy"

psql_remote() {
    remote "docker exec -i $DB_CONTAINER psql -q -v ON_ERROR_STOP=1 -U marketmind -d marketmind"
}
sql_quote() { local value="${1//\'/\'\'}"; printf "'%s'" "$value"; }

# --- Ruoli -------------------------------------------------------------------
# I ruoli devono esistere prima del restore di un dump (i GRANT del dump li
# referenziano), mentre i grant sugli schema di init-roles.sql.tmpl
# richiedono schema già esistenti: con --db-only si creano solo i ruoli.
# Anche la password del superuser si riallinea qui: POSTGRES_PASSWORD_FILE
# vale solo alla prima inizializzazione del cluster.
if [ "$KEEP_SECRETS" = "1" ]; then
    log "--keep-secrets: password dei ruoli lasciate invariate"
else
    log "ruoli e password"
    [ -n "${MARKETMIND_READONLY_PASSWORD:-}" ] || fail "\$MARKETMIND_READONLY_PASSWORD non impostata in $ENV_FILE"
    {
        for role in marketmind_ingestion marketmind_app marketmind_readonly; do
            echo "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$role') THEN CREATE ROLE $role LOGIN; END IF; END \$\$;"
        done
        echo "ALTER ROLE marketmind PASSWORD $(sql_quote "$MARKETMIND_DB_PASSWORD");"
        echo "ALTER ROLE marketmind_ingestion PASSWORD $(sql_quote "$MARKETMIND_INGESTION_PASSWORD");"
        echo "ALTER ROLE marketmind_app PASSWORD $(sql_quote "$MARKETMIND_APP_PASSWORD");"
        echo "ALTER ROLE marketmind_readonly PASSWORD $(sql_quote "$MARKETMIND_READONLY_PASSWORD");"
    } | psql_remote || fail "creazione dei ruoli fallita"

    if [ "$DB_ONLY" = "0" ]; then
        log "grant dei ruoli applicativi (init-roles.sql.tmpl)"
        rendered_sql="$(<"$ROLES_SQL_TEMPLATE")"
        rendered_sql="${rendered_sql//%%MARKETMIND_INGESTION_PASSWORD%%/$MARKETMIND_INGESTION_PASSWORD}"
        rendered_sql="${rendered_sql//%%MARKETMIND_APP_PASSWORD%%/$MARKETMIND_APP_PASSWORD}"
        rendered_sql="${rendered_sql//%%MARKETMIND_READONLY_PASSWORD%%/$MARKETMIND_READONLY_PASSWORD}"
        printf '%s\n' "$rendered_sql" | psql_remote || fail "init-roles.sql.tmpl fallito"
    fi
fi

if [ "$DB_ONLY" = "1" ]; then
    log "--db-only: niente migrazioni né crontab"
    exit 0
fi

for image in "$MIGRATE_IMAGE" "$PIPELINES_IMAGE" "$DECIDE_IMAGE"; do
    remote "docker image inspect $image >/dev/null 2>&1" || fail "immagine $image assente sul server: serve --build"
done

# --- Migrazioni: prima di installare la crontab ------------------------------
log "migrazioni"
remote "$(job_line migrate)" >/dev/null || fail "migrazioni fallite — vedi: ssh $SSH_HOST docker logs marketmind-migrate"
log "schema aggiornato"

# --- Crontab -----------------------------------------------------------------
if [ "$NO_CRON" = "1" ]; then
    log "--no-cron: crontab non toccata"
else
    log "installo la crontab"
    { current_crontab_without_block; crontab_block; } | remote "crontab -" || fail "installazione della crontab fallita"
fi

log "deploy completato"
