"""Test unitari del dispatcher (`marketmind_frontend.dispatch`): la parte che
gira nel container frontend, prende in carico una richiesta e stampa la unit
systemd da avviare. Lo script sull'host la avvia; qui il DB è finto."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from marketmind_frontend import dispatch
from marketmind_frontend.jobs import JobRequest


def _req(kind, target, request_id=17):
    return JobRequest(request_id, kind, target, None, "started", None, None)


@pytest.mark.parametrize(
    "kind, target, unit",
    [
        ("pipeline", "fred", "marketmind-ingest-fred.service"),
        ("decide", None, "marketmind-decide.service"),
        ("init-portfolio", "4", "marketmind-init-portfolio@4.service"),
        ("backtest", "12", "marketmind-backtest@12.service"),
    ],
)
def test_unit_for(kind, target, unit):
    assert dispatch.unit_for(_req(kind, target)) == unit


class FakeQueue:
    def __init__(self, claimed=None):
        self.claimed = claimed
        self.failed = []

    def claim(self, *, now):
        return self.claimed

    def fail(self, request_id, message, *, now):
        self.failed.append((request_id, message))


@pytest.fixture
def queue(mocker):
    fake = FakeQueue()

    @contextmanager
    def transaction():
        yield object()

    db = mocker.Mock()
    db.transaction = transaction
    mocker.patch.object(dispatch, "_admin_database", return_value=db)
    mocker.patch.object(dispatch, "JobQueue", return_value=fake)
    return fake


def test_claim_stampa_id_e_unit(queue, capsys):
    queue.claimed = _req("backtest", "4")

    assert dispatch.main(["claim"]) == 0

    assert capsys.readouterr().out == "17 marketmind-backtest@4.service\n"


def test_claim_senza_richieste_non_stampa_nulla(queue, capsys):
    assert dispatch.main(["claim"]) == 0

    assert capsys.readouterr().out == ""


def test_fail_registra_il_motivo(queue):
    assert dispatch.main(["fail", "17", "Unit", "not", "found"]) == 0

    assert queue.failed == [(17, "Unit not found")]
