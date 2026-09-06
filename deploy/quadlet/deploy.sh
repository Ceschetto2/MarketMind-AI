#!/usr/bin/env bash
# Deploy/refresh delle unit Podman Quadlet di MarketMind-AI — oggi
# marketmind-db (Postgres+TimescaleDB) più i due ruoli applicativi che ci
# vivono sopra; le future unit delle pipeline di ingestion si aggiungono
# allo stesso schema quando arrivano (raccolte automaticamente, vedi
# `UNITS` sotto — non serve editare questo file per una nuova pipeline).
#
# Idempotente: rilanciarlo aggiorna i symlink, ricrea sempre i secret dal
# valore corrente delle variabili d'ambiente (mai "esiste già, non tocco":
# un secret disallineato da una password cambiata è la classe di bug che
# questo evita, agenda #28) e riallinea i due ruoli Postgres applicativi,
# riavviando il servizio solo se richiesto esplicitamente. Pensato per
# girare sia a mano in locale sia, in futuro, come step di un workflow
# GitHub Actions (runner self-hosted sul server di destinazione, o SSH —
# decisione ancora aperta, vedi `Market Mind AI - Docs/Architettura/02_ci_cd.md`
# e `agenda.md`): per questo non è interattivo se le variabili password
# sono già nell'ambiente, e termina con exit code non zero su qualunque
# errore.
#
# Uso:
#   deploy/quadlet/deploy.sh                     # symlink + secret + ruoli + enable --now (idempotente)
#   deploy/quadlet/deploy.sh --restart           # come sopra ma forza un restart di marketmind-db
#   deploy/quadlet/deploy.sh --trigger <pipeline>  # avvia subito marketmind-ingest-<pipeline>.service
#                                                   # (run manuale ad hoc, non aspetta il timer);
#                                                   # non esegue il resto del deploy, vedi nota sotto
#
# Variabili d'ambiente:
#   MARKETMIND_DB_PASSWORD          password per il podman secret del DB
#                                    (marketmind-db-password). Se manca e
#                                    siamo in un terminale interattivo,
#                                    viene chiesta a mano; in CI (nessun
#                                    tty) è obbligatoria, altrimenti lo
#                                    script fallisce.
#   MARKETMIND_INGESTION_PASSWORD   password del ruolo Postgres applicativo
#                                    marketmind_ingestion (scrittura
#                                    market_data+raw+audit). Stessa logica
#                                    interattiva/obbligatoria di sopra.
#   MARKETMIND_APP_PASSWORD         password del ruolo Postgres applicativo
#                                    marketmind_app (scrittura
#                                    decisions+portfolio, lettura
#                                    market_data). Stessa logica di sopra.
#   FINNHUB_API_KEY                 chiave API Finnhub, obbligatoria (nessun
#   FRED_API_KEY                    fallback interattivo: sono chiavi di
#   FMP_API_KEY                     terze parti, non password scelte da chi
#                                    lancia il deploy) — già in .env/.env.example.
#   MARKETMIND_QUADLET_DIR           dove linkare le unit (default
#                                    ~/.config/containers/systemd, la
#                                    posizione che systemd --user si aspetta).
#
# Nota su `--trigger`: esegue solo `systemctl --user start
# marketmind-ingest-<pipeline>.service` ed esce — non rilinka le unit, non
# tocca i secret, non riallinea i ruoli. Scelta deliberata: `--trigger` è
# pensato per un run ad hoc di una pipeline già deployata (vedi
# `Market Mind AI - Docs/pipelines/01_trigger_e_scheduling.md`), non per
# essere un deploy completo travestito da flag diverso — se le unit non
# sono ancora linkate/abilitate, `systemctl --user start` fallisce con un
# errore chiaro ("Unit ... not found") che indica di lanciare prima un
# deploy senza `--trigger`, invece di eseguire silenziosamente un intero
# ciclo di deploy (symlink+secret+ruoli, potenzialmente anche un restart
# involontario di marketmind-db) ogni volta che si vuole solo forzare un
# giro di una pipeline.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
QUADLET_SRC="$REPO_ROOT/deploy/quadlet"
QUADLET_DST="${MARKETMIND_QUADLET_DIR:-$HOME/.config/containers/systemd}"
DB_CONTAINER_NAME="marketmind-db"
SERVICE_NAME="marketmind-db.service"
ROLES_SQL_TEMPLATE="$QUADLET_SRC/init-roles.sql.tmpl"

log() { printf '[deploy] %s\n' "$*" >&2; }
fail() { log "ERRORE: $*"; exit 1; }

# --- --trigger <pipeline>: run ad hoc, esce subito (vedi nota in testa al file) ---
if [ "${1:-}" = "--trigger" ]; then
    pipeline="${2:-}"
    [ -n "$pipeline" ] || fail "--trigger richiede il nome di una pipeline, es.: --trigger yfinance-prices"
    command -v systemctl >/dev/null 2>&1 || fail "systemctl non trovato in PATH"
    unit="marketmind-ingest-${pipeline}.service"
    log "avvio $unit"
    systemctl --user start "$unit"
    log "$unit avviato"
    exit 0
fi

command -v podman >/dev/null 2>&1 || fail "podman non trovato in PATH"
command -v systemctl >/dev/null 2>&1 || fail "systemctl non trovato in PATH"

# --- Unit Quadlet: glob dinamico, non lista fissa ---
# Raccoglie automaticamente ogni .container/.timer/.network/.volume presente
# in deploy/quadlet — comprese le future unit per pipeline di ingestion
# (es. marketmind-ingest-yfinance-prices.{container,timer}) senza dover
# editare questo script a ogni nuova pipeline.
UNITS=()
shopt -s nullglob
for pattern in '*.container' '*.timer' '*.network' '*.volume'; do
    for f in "$QUADLET_SRC"/$pattern; do
        UNITS+=("$(basename "$f")")
    done
done
shopt -u nullglob
[ "${#UNITS[@]}" -gt 0 ] || fail "nessuna unit Quadlet trovata in $QUADLET_SRC"

log "linking unit Quadlet: $QUADLET_SRC -> $QUADLET_DST"
mkdir -p "$QUADLET_DST"
for unit in "${UNITS[@]}"; do
    src="$QUADLET_SRC/$unit"
    dst="$QUADLET_DST/$unit"
    if [ -L "$dst" ] && [ "$(readlink -f "$dst")" = "$(readlink -f "$src")" ]; then
        log "  $unit già collegata"
    else
        ln -sf "$src" "$dst"
        log "  $unit collegata"
    fi
done

# --- Secret: sempre ricreati dal valore corrente dell'ambiente ---
# store_secret <secret_name> <env_var_name> <allow_interactive:0|1>
#
# Non c'è più un ramo "esiste già, non tocco" (agenda #28): un secret
# Podman è immutabile una volta creato, quindi l'unico modo per riallinearlo
# a una password cambiata in .env è rimuoverlo e ricrearlo — sempre, anche
# se non è cambiato nulla (idempotente lo stesso: ricreare con lo stesso
# valore è un no-op osservabile). Il valore non tocca mai un file su disco
# né un argomento di riga di comando: solo `printf` (builtin bash, nessun
# processo con la password nei suoi argv) in pipe verso `podman secret
# create <nome> -`, che la legge da stdin.
store_secret() {
    local secret_name="$1" var_name="$2" allow_interactive="$3"
    local value="${!var_name:-}"

    if podman secret exists "$secret_name" 2>/dev/null; then
        podman secret rm "$secret_name" >/dev/null
        log "  secret $secret_name rimosso (verrà ricreato)"
    fi

    if [ -n "$value" ]; then
        printf '%s' "$value" | podman secret create "$secret_name" - >/dev/null
        log "  secret $secret_name (ri)creato da \$$var_name"
    elif [ "$allow_interactive" = "1" ] && [ -t 0 ]; then
        log "  secret $secret_name: \$$var_name mancante — incollala e premi Ctrl-D:"
        podman secret create "$secret_name" -
    else
        fail "\$$var_name non impostata: necessaria per (ri)creare il secret $secret_name (obbligatoria in modalità non interattiva/CI)"
    fi
}

log "provisioning secret"
store_secret marketmind-db-password MARKETMIND_DB_PASSWORD 1
store_secret marketmind-ingestion-password MARKETMIND_INGESTION_PASSWORD 1
store_secret marketmind-app-password MARKETMIND_APP_PASSWORD 1
# Le tre chiavi API sono di terze parti, non password scelte da chi lancia
# il deploy: nessun fallback interattivo, vanno già valorizzate in .env.
store_secret marketmind-finnhub-api-key FINNHUB_API_KEY 0
store_secret marketmind-fred-api-key FRED_API_KEY 0
store_secret marketmind-fmp-api-key FMP_API_KEY 0

log "systemctl --user daemon-reload"
systemctl --user daemon-reload

if [ "${1:-}" = "--restart" ]; then
    log "restart $SERVICE_NAME"
    systemctl --user restart "$SERVICE_NAME"
else
    # Le unit generate da Quadlet sono "transient/generated": systemd le
    # abilita già da sé al daemon-reload processando l'[Install] della
    # unit .container — `systemctl enable` su una unit così fallisce con
    # "Unit ... is transient or generated". Basta (ed è idempotente) `start`.
    log "start $SERVICE_NAME (no-op se già attivo)"
    systemctl --user start "$SERVICE_NAME"
fi

if [ "$(loginctl show-user "$(id -un)" --property=Linger --value 2>/dev/null)" != "yes" ]; then
    log "ATTENZIONE: lingering non abilitato per $(id -un) — il servizio si fermerà al logout." \
        "Abilitalo una tantum con: loginctl enable-linger $(id -un)"
fi

log "attendo che $SERVICE_NAME sia healthy..."
db_healthy=0
for _ in $(seq 1 30); do
    if systemctl --user is-active --quiet "$SERVICE_NAME" \
        && [ "$(podman inspect --format '{{.State.Health.Status}}' "$DB_CONTAINER_NAME" 2>/dev/null)" = "healthy" ]; then
        log "$SERVICE_NAME attivo e healthy"
        db_healthy=1
        break
    fi
    sleep 2
done
[ "$db_healthy" = "1" ] || fail "$SERVICE_NAME non è diventato healthy entro il timeout (60s) — controlla con: journalctl --user -u $SERVICE_NAME"

# --- Ruoli Postgres applicativi (agenda #27, db/03_utenti_db.md) ---
# Il template ha le password come placeholder, sostituite qui con la sola
# espansione di variabile nativa di bash (${var//pattern/replacement}) —
# nessun sed/envsubst esterno che le riceverebbe come argomento di riga di
# comando: il testo risultante va in pipe via stdin a `podman exec -i ...
# psql`, mai su disco, mai negli argv di un processo.
#
# Richiede che le due variabili siano valorizzate nell'ambiente (non basta
# averle fornite solo interattivamente a `podman secret create` sopra, che
# le legge da stdin senza mai popolare la variabile di shell): è l'unico
# punto dello script dove il valore in chiaro serve una seconda volta, per
# l'ALTER ROLE ... PASSWORD.
: "${MARKETMIND_INGESTION_PASSWORD:=}"
: "${MARKETMIND_APP_PASSWORD:=}"
[ -n "$MARKETMIND_INGESTION_PASSWORD" ] || fail "MARKETMIND_INGESTION_PASSWORD non impostata nell'ambiente — necessaria per allineare la password del ruolo marketmind_ingestion (l'inserimento interattivo del solo secret non basta)"
[ -n "$MARKETMIND_APP_PASSWORD" ] || fail "MARKETMIND_APP_PASSWORD non impostata nell'ambiente — necessaria per allineare la password del ruolo marketmind_app (l'inserimento interattivo del solo secret non basta)"

log "inizializzo/allineo i ruoli applicativi (marketmind_ingestion, marketmind_app)"
[ -f "$ROLES_SQL_TEMPLATE" ] || fail "manca $ROLES_SQL_TEMPLATE"
rendered_sql="$(<"$ROLES_SQL_TEMPLATE")"
rendered_sql="${rendered_sql//%%MARKETMIND_INGESTION_PASSWORD%%/$MARKETMIND_INGESTION_PASSWORD}"
rendered_sql="${rendered_sql//%%MARKETMIND_APP_PASSWORD%%/$MARKETMIND_APP_PASSWORD}"
printf '%s\n' "$rendered_sql" | podman exec -i "$DB_CONTAINER_NAME" psql -U marketmind -d marketmind \
    || fail "init-roles.sql.tmpl fallito — vedi output psql sopra"
log "ruoli applicativi allineati"

exit 0
