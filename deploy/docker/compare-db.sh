#!/usr/bin/env bash
# Confronta il DB del laptop (container Podman marketmind-db) con quello del
# server (container Docker marketmind-db via SSH) dopo un restore: conteggio
# esatto delle righe di ogni tabella dei cinque schema applicativi, ultima
# barra dei prezzi, revisione Alembic e stato dei portfolio. Exit 0 solo se
# tutto coincide. Ha senso a scritture ferme su entrambi i lati.
#
# Uso: deploy/docker/compare-db.sh

set -euo pipefail

SSH_HOST="${MARKETMIND_SSH_HOST:-marketmind}"

read -r -d '' QUERY <<'SQL' || true
SELECT 'rows ' || table_schema || '.' || table_name || ' ' ||
       (xpath('/row/c/text()', query_to_xml(
           format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name), false, true, '')))[1]::text
FROM information_schema.tables
WHERE table_schema IN ('market_data', 'raw', 'decisions', 'portfolio', 'audit')
  AND table_type = 'BASE TABLE'
UNION ALL
SELECT 'alembic ' || version_num FROM public.alembic_version
UNION ALL
SELECT 'last_price ' || max(ts)::text FROM market_data.t_market_prices
UNION ALL
SELECT 'portfolio ' || name || ' cash=' || cash::text || ' next=' || coalesce(next_decision_at::text, 'NULL')
       || ' positions=' || (SELECT count(*) FROM portfolio.t_portfolio_positions pp WHERE pp.portfolio_id = p.portfolio_id)::text
FROM portfolio.t_portfolios p
ORDER BY 1;
SQL

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

printf '%s\n' "$QUERY" | podman exec -i marketmind-db psql -X -At -U marketmind -d marketmind > "$tmp/laptop"
printf '%s\n' "$QUERY" | ssh "$SSH_HOST" "docker exec -i marketmind-db psql -X -At -U marketmind -d marketmind" > "$tmp/server"

if diff -u --label laptop --label server "$tmp/laptop" "$tmp/server"; then
    echo "[compare-db] identici: $(grep -c '^rows ' "$tmp/laptop") tabelle"
    grep -v '^rows ' "$tmp/laptop"
else
    echo "[compare-db] DIFFERENZE (sopra)" >&2
    exit 1
fi
