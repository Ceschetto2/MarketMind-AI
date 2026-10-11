#!/usr/bin/env bash
# Dispatcher della coda dei job della dashboard, lato host (deploy Quadlet).
#
# Lanciato ogni minuto da marketmind-dispatch.timer. Per ogni richiesta
# pendente in ops.t_job_requests: il container frontend la prende in carico e
# stampa "<id> <unit>" (python -m marketmind_frontend.dispatch claim); questo
# script controlla che la unit abbia la forma attesa e la avvia con
# `systemctl --user start --no-block`, cioè con gli stessi container, ruoli e
# secret dei timer. Se l'avvio fallisce lo registra sulla richiesta
# (`dispatch fail`). Il container non ha accesso all'host, e l'host non
# decide quale job eseguire: esegue solo unit marketmind-*.
#
# Al massimo MAX_PER_RUN richieste per giro, così un giro non resta aperto a
# lungo; le altre partono al minuto successivo.
set -euo pipefail

MAX_PER_RUN=10
IMAGE="localhost/marketmind-frontend:latest"
UNIT_PATTERN='^marketmind-[a-z0-9-]+(@[1-9][0-9]*)?\.service$'

dispatch() {
    podman run --rm --network marketmind \
        --secret marketmind-app-password,type=env,target=MARKETMIND_APP_PASSWORD \
        -e POSTGRES_HOST=marketmind-db -e POSTGRES_PORT=5432 -e POSTGRES_DB=marketmind \
        "$IMAGE" python -m marketmind_frontend.dispatch "$@"
}

for _ in $(seq 1 "$MAX_PER_RUN"); do
    line="$(dispatch claim)"
    [ -n "$line" ] || exit 0
    read -r request_id unit extra <<<"$line"
    if ! [[ "$request_id" =~ ^[0-9]+$ && "$unit" =~ $UNIT_PATTERN && -z "${extra:-}" ]]; then
        echo "risposta del dispatcher non valida, ignorata: $line" >&2
        [[ "$request_id" =~ ^[0-9]+$ ]] && dispatch fail "$request_id" "unit non valida: $unit" || true
        continue
    fi
    if output="$(systemctl --user start --no-block "$unit" 2>&1)"; then
        echo "richiesta $request_id: avviata $unit"
    else
        echo "richiesta $request_id: avvio di $unit fallito: $output" >&2
        dispatch fail "$request_id" "systemctl --user start $unit: $output" || true
    fi
done
