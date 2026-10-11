"""Test dello script del dispatcher lato host
(`deploy/systemd/marketmind_frontend/marketmind-dispatch.sh`).

`podman` e `systemctl` sono sostituiti da eseguibili finti in testa al PATH:
il `podman` finto restituisce a ogni `claim` la riga successiva di una coda
preparata dal test e registra le chiamate `fail`; il `systemctl` finto
registra le unit avviate e fallisce per quelle indicate. Nessun container e
nessuna unit reale.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/systemd/marketmind_frontend/marketmind-dispatch.sh"

FAKE_PODMAN = """#!/usr/bin/env bash
# Ultimi argomenti: ... dispatch claim | dispatch fail <id> <msg...>
args=("$@")
for i in "${!args[@]}"; do
    if [ "${args[$i]}" = "marketmind_frontend.dispatch" ]; then cmd=("${args[@]:$((i + 1))}"); fi
done
if [ "${cmd[0]}" = "claim" ]; then
    line="$(head -n 1 "$FAKE_DIR/queue")"
    tail -n +2 "$FAKE_DIR/queue" > "$FAKE_DIR/queue.tmp" && mv "$FAKE_DIR/queue.tmp" "$FAKE_DIR/queue"
    [ -n "$line" ] && echo "$line"
    exit 0
fi
echo "${cmd[*]}" >> "$FAKE_DIR/fails"
"""

FAKE_SYSTEMCTL = """#!/usr/bin/env bash
unit="${@: -1}"
if grep -qx "$unit" "$FAKE_DIR/broken" 2>/dev/null; then echo "Unit $unit not found." >&2; exit 5; fi
echo "$*" >> "$FAKE_DIR/started"
"""


@pytest.fixture
def run(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("podman", FAKE_PODMAN), ("systemctl", FAKE_SYSTEMCTL)):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)

    def _run(queue_lines, broken=()):
        (tmp_path / "queue").write_text("".join(f"{line}\n" for line in queue_lines))
        (tmp_path / "broken").write_text("".join(f"{u}\n" for u in broken))
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_DIR": str(tmp_path)}
        result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)

        def read(name):
            p = tmp_path / name
            return p.read_text().splitlines() if p.exists() else []

        return result, read("started"), read("fails")

    return _run


def test_avvia_le_unit_richieste_in_ordine(run):
    result, started, fails = run(["17 marketmind-backtest@4.service", "18 marketmind-ingest-fred.service"])

    assert result.returncode == 0
    assert started == ["--user start --no-block marketmind-backtest@4.service",
                       "--user start --no-block marketmind-ingest-fred.service"]
    assert fails == []


def test_nessuna_richiesta_nessuna_azione(run):
    result, started, fails = run([])

    assert (result.returncode, started, fails) == (0, [], [])


def test_avvio_fallito_registrato_sulla_richiesta(run):
    result, started, fails = run(["17 marketmind-ingest-fred.service"], broken=["marketmind-ingest-fred.service"])

    assert result.returncode == 0 and started == []
    assert fails == ["fail 17 systemctl --user start marketmind-ingest-fred.service: Unit marketmind-ingest-fred.service not found."]


@pytest.mark.parametrize("line", ["17 sshd.service", "17 marketmind-decide.service; rm -rf ~",
                                  "17 marketmind-backtest@0.service", "17 ../marketmind-x.service"])
def test_unit_non_valida_non_si_avvia(run, line):
    result, started, fails = run([line])

    assert started == []
    assert len(fails) == 1 and fails[0].startswith("fail 17 unit non valida")
