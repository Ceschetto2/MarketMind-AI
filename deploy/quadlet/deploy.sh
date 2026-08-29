#!/usr/bin/env bash
# Deploy/refresh delle unit Podman Quadlet di MarketMind-AI — oggi solo
# marketmind-db (Postgres+TimescaleDB), altre unit si aggiungono allo
# stesso schema quando arrivano.
#
# Idempotente: rilanciarlo aggiorna i symlink e riavvia il servizio solo se
# necessario, senza duplicare nulla. Pensato per girare sia a mano in
# locale sia, in futuro, come step di un workflow GitHub Actions (runner
# self-hosted sul server di destinazione, o SSH — decisione ancora aperta,
# vedi `Market Mind AI - Docs/Architettura/02_ci_cd.md` e `agenda.md`):
# per questo non è interattivo se `MARKETMIND_DB_PASSWORD` è già
# nell'ambiente, e termina con exit code non zero su qualunque errore.
#
# Uso:
#   deploy/quadlet/deploy.sh            # symlink + enable --now (idempotente)
#   deploy/quadlet/deploy.sh --restart  # come sopra ma forza un restart
#
# Variabili d'ambiente:
#   MARKETMIND_DB_PASSWORD   password per il podman secret del DB. Se il
#                            secret esiste già viene ignorata. Se manca e
#                            siamo in un terminale interattivo, viene
#                            chiesta a mano; in CI (nessun tty) è
#                            obbligatoria, altrimenti lo script fallisce.
#   MARKETMIND_QUADLET_DIR   dove linkare le unit (default
#                            ~/.config/containers/systemd, la posizione che
#                            systemd --user si aspetta).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
QUADLET_SRC="$REPO_ROOT/deploy/quadlet"
QUADLET_DST="${MARKETMIND_QUADLET_DIR:-$HOME/.config/containers/systemd}"
SECRET_NAME="marketmind-db-password"
SERVICE_NAME="marketmind-db.service"
UNITS=(marketmind-db.container marketmind-db-data.volume)

log() { printf '[deploy] %s\n' "$*" >&2; }
fail() { log "ERRORE: $*"; exit 1; }

command -v podman >/dev/null 2>&1 || fail "podman non trovato in PATH"
command -v systemctl >/dev/null 2>&1 || fail "systemctl non trovato in PATH"

log "linking unit Quadlet: $QUADLET_SRC -> $QUADLET_DST"
mkdir -p "$QUADLET_DST"
for unit in "${UNITS[@]}"; do
    src="$QUADLET_SRC/$unit"
    dst="$QUADLET_DST/$unit"
    [ -f "$src" ] || fail "manca $src"
    if [ -L "$dst" ] && [ "$(readlink -f "$dst")" = "$(readlink -f "$src")" ]; then
        log "  $unit già collegata"
    else
        ln -sf "$src" "$dst"
        log "  $unit collegata"
    fi
done

if podman secret exists "$SECRET_NAME" 2>/dev/null; then
    log "secret $SECRET_NAME già presente, non tocco"
elif [ -n "${MARKETMIND_DB_PASSWORD:-}" ]; then
    printf '%s' "$MARKETMIND_DB_PASSWORD" | podman secret create "$SECRET_NAME" -
    log "secret $SECRET_NAME creato da MARKETMIND_DB_PASSWORD"
elif [ -t 0 ]; then
    log "secret $SECRET_NAME mancante — incollala e premi Ctrl-D:"
    podman secret create "$SECRET_NAME" -
else
    fail "secret $SECRET_NAME mancante e MARKETMIND_DB_PASSWORD non impostata (obbligatoria in modalità non interattiva/CI)"
fi

log "systemctl --user daemon-reload"
systemctl --user daemon-reload

if [ "${1:-}" = "--restart" ]; then
    log "restart $SERVICE_NAME"
    systemctl --user restart "$SERVICE_NAME"
else
    log "enable --now $SERVICE_NAME (no-op se già attivo)"
    systemctl --user enable --now "$SERVICE_NAME"
fi

if [ "$(loginctl show-user "$(id -un)" --property=Linger --value 2>/dev/null)" != "yes" ]; then
    log "ATTENZIONE: lingering non abilitato per $(id -un) — il servizio si fermerà al logout." \
        "Abilitalo una tantum con: loginctl enable-linger $(id -un)"
fi

log "attendo che $SERVICE_NAME sia healthy..."
for _ in $(seq 1 30); do
    if systemctl --user is-active --quiet "$SERVICE_NAME" \
        && [ "$(podman inspect --format '{{.State.Health.Status}}' marketmind-db 2>/dev/null)" = "healthy" ]; then
        log "$SERVICE_NAME attivo e healthy"
        exit 0
    fi
    sleep 2
done
fail "$SERVICE_NAME non è diventato healthy entro il timeout (60s) — controlla con: journalctl --user -u $SERVICE_NAME"
