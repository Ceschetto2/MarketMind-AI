"""Test unitari delle classi base delle pipeline (`marketmind_pipelines.base`).

Nessun DB: `IngestionRunAudit` è sostituito da un finto audit che registra
l'esito dichiarato, e `load()` è ridefinito nelle pipeline di prova.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from marketmind_pipelines import base
from marketmind_pipelines.base import BasePipeline, BulkPipeline, PerSymbolPipeline


class FakeAudit:
    instances: list[FakeAudit] = []

    def __init__(self, db, source, target_table):
        self.source, self.target_table = source, target_table
        self.run_id = 99
        self.rows_written = 0
        self.outcome: tuple[str, str] | None = None
        self.exc: BaseException | None = None
        FakeAudit.instances.append(self)

    def mark_partial(self, message):
        self.outcome = ("partial", message)

    def mark_failed(self, message):
        self.outcome = ("failed", message)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exc = exc
        return False


@pytest.fixture(autouse=True)
def fake_audit(monkeypatch):
    FakeAudit.instances = []
    monkeypatch.setattr(base, "IngestionRunAudit", FakeAudit)


class ListPipeline(BasePipeline[str, str, str]):
    """Target = stringhe; un target che inizia con `!` fallisce in extract."""

    name = "test-list"
    audit_source = "fred"
    target_table = "market_data.t_macro_events"

    def __init__(self, items: Sequence[str]):
        super().__init__(db=None)
        self.items = items
        self.loaded: list[list[str]] = []
        self.calls: list[str] = []

    def setup(self):
        self.calls.append("setup")

    def teardown(self):
        self.calls.append("teardown")

    def targets(self):
        return self.items

    def extract(self, target):
        if target.startswith("!"):
            raise RuntimeError(f"fetch {target} fallito")
        return target

    def transform(self, target, raw):
        return [] if raw == "vuoto" else [raw.upper()]

    def load(self, records):
        self.loaded.append(list(records))
        return len(records)


class TestEsito:
    @pytest.mark.parametrize(
        ("total", "failed", "expected"),
        [(0, 0, "success"), (4, 0, "success"), (4, 1, "partial"), (4, 2, "partial"), (4, 3, "failed"), (1, 1, "failed")],
    )
    def test_soglia(self, total, failed, expected):
        assert BasePipeline.outcome(total, failed) == expected

    def test_tutto_ok_e_success(self):
        result = ListPipeline(["a", "b"]).run()

        assert result.status == "success"
        assert result.rows_written == 2
        assert FakeAudit.instances[0].outcome is None
        assert FakeAudit.instances[0].rows_written == 2

    def test_un_target_fallito_e_partial(self):
        pipeline = ListPipeline(["a", "!b", "c"])

        result = pipeline.run()

        assert result.status == "partial"
        assert [f.target for f in result.failures] == ["!b"]
        assert pipeline.loaded == [["A"], ["C"]]
        status, message = FakeAudit.instances[0].outcome
        assert status == "partial"
        assert "1/3" in message and "fetch !b fallito" in message

    def test_oltre_soglia_e_failed(self):
        result = ListPipeline(["!a", "!b", "c"]).run()

        assert result.status == "failed"
        assert FakeAudit.instances[0].outcome[0] == "failed"

    def test_record_vuoti_non_sono_un_fallimento_e_non_caricano(self):
        pipeline = ListPipeline(["vuoto"])

        assert pipeline.run().status == "success"
        assert pipeline.loaded == []


class TestCicloDiVita:
    def test_setup_e_teardown(self):
        pipeline = ListPipeline(["a"])
        pipeline.run()

        assert pipeline.calls == ["setup", "teardown"]

    def test_eccezione_fuori_dai_target_fa_fallire_il_run_e_rilancia(self):
        class Broken(ListPipeline):
            def targets(self):
                raise RuntimeError("universo illeggibile")

        pipeline = Broken([])
        with pytest.raises(RuntimeError, match="universo illeggibile"):
            pipeline.run()

        assert pipeline.calls == ["setup", "teardown"]
        assert isinstance(FakeAudit.instances[0].exc, RuntimeError)

    def test_interruzione_non_viene_isolata_come_errore_di_target(self):
        """Un SIGTERM (convertito in `PipelineInterrupted`) deve chiudere il
        run, non essere trattato come un singolo target fallito."""

        class Interrupted(ListPipeline):
            def extract(self, target):
                raise base.PipelineInterrupted("SIGTERM")

        with pytest.raises(base.PipelineInterrupted):
            Interrupted(["a", "b"]).run()
        assert isinstance(FakeAudit.instances[0].exc, base.PipelineInterrupted)

    def test_audit_con_source_e_tabella_della_pipeline(self):
        ListPipeline(["a"]).run()

        audit = FakeAudit.instances[0]
        assert (audit.source, audit.target_table) == ("fred", "market_data.t_macro_events")


class SymbolPipeline(PerSymbolPipeline[str, str]):
    name = "test-symbols"
    audit_source = "yfinance"
    target_table = "market_data.t_market_prices"
    delay_between_targets = (1.0, 2.0)

    def default_symbols(self):
        return ["UNIVERSO1", "UNIVERSO2"]

    def extract(self, target):
        return target

    def transform(self, target, raw):
        return [raw]

    def load(self, records):
        return len(records)


class TestPerSymbolPipeline:
    def test_simboli_di_default_dallUniverso(self):
        assert SymbolPipeline(db=None, sleep=lambda _: None).targets() == ["UNIVERSO1", "UNIVERSO2"]

    def test_simboli_espliciti_sostituiscono_luniverso(self):
        assert SymbolPipeline(db=None, symbols=["AAPL"], sleep=lambda _: None).targets() == ["AAPL"]

    def test_pausa_tra_i_target_non_prima_del_primo(self):
        waits: list[float] = []

        SymbolPipeline(db=None, symbols=["A", "B", "C"], sleep=waits.append).run()

        assert len(waits) == 2
        assert all(1.0 <= w <= 2.0 for w in waits)


class OneShot(BulkPipeline[dict, str]):
    name = "test-bulk"
    audit_source = "finnhub"
    target_table = "market_data.t_company_events"

    def __init__(self, payload):
        super().__init__(db=None)
        self.payload = payload

    def fetch(self):
        if self.payload is None:
            raise RuntimeError("API giù")
        return self.payload

    def parse(self, raw):
        return list(raw)

    def load(self, records):
        return len(records)


class TestBulkPipeline:
    def test_un_solo_target(self):
        result = OneShot({"a": 1, "b": 2}).run()

        assert (result.status, result.targets_total, result.rows_written) == ("success", 1, 2)

    def test_fetch_fallito_e_failed(self):
        result = OneShot(None).run()

        assert result.status == "failed"
        assert result.failures[0].target == "test-bulk"
