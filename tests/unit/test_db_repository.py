"""Test unitari dei safeguard di `TableRepository` (`marketmind_db.repository`).

Nessun accesso a Postgres: la sessione è un mock, e dove serve verificare
lo statement generato lo si compila col dialetto Postgres. Il comportamento
reale degli upsert contro il DB vive in
`tests/integration/test_db_repository.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import column, func, text
from sqlalchemy.dialects import postgresql

from marketmind_db.access import FULL_ACCESS, INGESTION, READ_ONLY, AccessPolicy
from marketmind_db.exceptions import AccessDeniedError, InvalidQueryError
from marketmind_db.models.market_data import Asset, CompanyEvent, MacroEvent, MarketPrice, NewsEvent
from marketmind_db.models.portfolio import Portfolio
from marketmind_db.repository import TableRepository

_NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def _price(asset_id: int = 1, ts: datetime = _NOW, close: float = 10.0) -> dict:
    return {
        "asset_id": asset_id,
        "ts": ts,
        "source": "yfinance",
        "open": 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": close,
        "volume": 100,
        "fetched_at": _NOW,
    }


def _news(url: str = "https://x/1", headline: str = "h") -> dict:
    return {
        "asset_id": 1,
        "source": "Finnhub",
        "ts": _NOW,
        "headline": headline,
        "url": url,
        "fetched_at": _NOW,
    }


def _sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


@pytest.fixture
def session(mocker):
    session = mocker.MagicMock()
    session.execute.return_value.rowcount = 1
    session.execute.return_value.all.return_value = [(1,)]
    return session


def _repo(session, model=MarketPrice, policy: AccessPolicy = INGESTION, **kwargs):
    return TableRepository(session, model, policy, **kwargs)


class TestIntrospezione:
    def test_candidate_keys_include_pk_e_vincoli_unique(self, session):
        keys = _repo(session, NewsEvent).candidate_keys

        assert frozenset({"news_event_id"}) in keys
        assert frozenset({"url"}) in keys

    def test_candidate_key_composito_da_unique_constraint(self, session):
        keys = _repo(session, CompanyEvent).candidate_keys

        assert frozenset({"asset_id", "ts", "event_type", "source"}) in keys

    def test_colonne_obbligatorie_escludono_pk_autoincrement_e_nullable(self, session):
        required = _repo(session, NewsEvent).required_columns

        assert required == {"source", "ts", "headline", "url", "fetched_at"}


class TestPolicyDiAccesso:
    def test_scrittura_fuori_dagli_schema_permessi_e_rifiutata(self, session):
        repo = _repo(session, Portfolio, INGESTION)

        with pytest.raises(AccessDeniedError, match="portfolio"):
            repo.update({"cash": 1.0}, where={"portfolio_id": 1})
        session.execute.assert_not_called()

    def test_read_only_non_puo_scrivere(self, session):
        with pytest.raises(AccessDeniedError):
            _repo(session, MarketPrice, READ_ONLY).insert([_price()])

    def test_read_only_puo_leggere(self, session):
        _repo(session, MarketPrice, READ_ONLY).select(where={"asset_id": 1})

        session.execute.assert_called_once()

    def test_full_access_scrive_ovunque(self, session):
        _repo(session, Portfolio, FULL_ACCESS).update({"cash": 1.0}, where={"portfolio_id": 1})

        session.execute.assert_called_once()


class TestValidazioneRighe:
    def test_colonna_sconosciuta_rifiutata(self, session):
        row = _price() | {"symbol": "AAPL"}

        with pytest.raises(InvalidQueryError, match="symbol"):
            _repo(session).insert([row])

    def test_colonna_obbligatoria_mancante_rifiutata(self, session):
        row = _price()
        del row["close"]

        with pytest.raises(InvalidQueryError, match="close"):
            _repo(session).insert([row])

    def test_righe_con_colonne_diverse_rifiutate(self, session):
        rows = [_news(), _news(url="https://x/2") | {"sentiment_score": 0.1}]

        with pytest.raises(InvalidQueryError, match="stesse colonne"):
            _repo(session, NewsEvent).insert(rows)

    def test_batch_vuoto_e_noop(self, session):
        assert _repo(session).insert([]) == 0
        assert _repo(session).upsert([], conflict_on=("asset_id", "ts", "source")) == 0
        session.execute.assert_not_called()


class TestUpsert:
    def test_conflict_on_deve_essere_una_chiave_reale(self, session):
        with pytest.raises(InvalidQueryError, match="conflict_on"):
            _repo(session).upsert([_price()], conflict_on=("asset_id", "ts"))

    def test_conflict_on_ordine_indifferente(self, session):
        _repo(session).upsert([_price()], conflict_on=("source", "asset_id", "ts"))

        session.execute.assert_called_once()

    def test_update_di_default_aggiorna_le_colonne_non_chiave_fornite(self, session):
        _repo(session, NewsEvent).upsert([_news()], conflict_on=("url",))

        sql = _sql(session.execute.call_args.args[0])
        assert "ON CONFLICT (url) DO UPDATE SET" in sql
        assert "headline = excluded.headline" in sql
        assert "url = excluded.url" not in sql
        # sentiment_score non è tra le colonne fornite: non va sovrascritta.
        assert "sentiment_score" not in sql

    def test_update_vuoto_diventa_do_nothing(self, session):
        _repo(session, NewsEvent).upsert([_news()], conflict_on=("url",), update=())

        assert "ON CONFLICT (url) DO NOTHING" in _sql(session.execute.call_args.args[0])

    def test_update_non_puo_toccare_la_chiave_di_conflitto(self, session):
        with pytest.raises(InvalidQueryError, match="url"):
            _repo(session, NewsEvent).upsert([_news()], conflict_on=("url",), update=("url",))

    def test_update_non_puo_toccare_la_primary_key(self, session):
        row = _news() | {"news_event_id": 5}

        with pytest.raises(InvalidQueryError, match="news_event_id"):
            _repo(session, NewsEvent).upsert(
                [row], conflict_on=("url",), update=("news_event_id",)
            )

    def test_update_solo_su_colonne_fornite(self, session):
        """Aggiornare una colonna non presente nelle righe la imposterebbe a
        NULL/default sulla riga esistente — un errore silenzioso."""
        with pytest.raises(InvalidQueryError, match="sentiment_score"):
            _repo(session, NewsEvent).upsert(
                [_news()], conflict_on=("url",), update=("sentiment_score",)
            )

    def test_duplicati_sulla_chiave_nello_stesso_batch_tiene_lultimo(self, session):
        rows = [_news(headline="vecchio"), _news(headline="nuovo")]

        _repo(session, NewsEvent).upsert(rows, conflict_on=("url",))

        params = session.execute.call_args.args[0].compile(dialect=postgresql.dialect()).params
        headlines = [v for k, v in params.items() if k.startswith("headline")]
        assert headlines == ["nuovo"]

    def test_upsert_returning_rifiuta_do_nothing(self, session):
        with pytest.raises(InvalidQueryError, match="returning"):
            _repo(session, NewsEvent).upsert_returning(
                [_news()], conflict_on=("url",), returning=("news_event_id",), update=()
            )

    def test_returning_su_colonna_sconosciuta_rifiutato(self, session):
        with pytest.raises(InvalidQueryError, match="nope"):
            _repo(session, NewsEvent).upsert_returning(
                [_news()], conflict_on=("url",), returning=("nope",)
            )


class TestChunking:
    def test_righe_divise_in_blocchi_da_batch_size(self, session):
        rows = [_price(asset_id=i) for i in range(5)]

        _repo(session, batch_size=2).insert(rows)

        assert session.execute.call_count == 3

    def test_blocco_limitato_dal_massimo_di_parametri_postgres(self, session):
        # 9 colonne per riga: con 65_535 parametri al massimo ci stanno
        # 7_281 righe per statement, anche se batch_size ne chiede di più.
        rows = [_price(asset_id=i) for i in range(7_282)]

        _repo(session, batch_size=100_000).insert(rows)

        assert session.execute.call_count == 2

    def test_righe_scritte_sommate_sui_blocchi(self, session):
        session.execute.return_value.all.return_value = [(1,), (2,)]

        assert _repo(session, batch_size=2).insert([_price(asset_id=i) for i in range(4)]) == 4

    def test_righe_scritte_contate_dal_returning_della_pk(self, session):
        """`rowcount` su una hypertable TimescaleDB vale -1: il conteggio
        passa dal RETURNING della primary key."""
        _repo(session).insert([_price()])

        assert "RETURNING" in _sql(session.execute.call_args.args[0])


class TestWhere:
    def test_mapping_genera_uguaglianze_in_e_is_null(self, session):
        _repo(session, NewsEvent).select(
            where={"source": "Finnhub", "asset_id": [1, 2], "sentiment_score": None}
        )

        sql = _sql(session.execute.call_args.args[0])
        assert "t_news_events.source = " in sql
        assert "t_news_events.asset_id IN" in sql
        assert "t_news_events.sentiment_score IS NULL" in sql

    def test_espressioni_sulle_colonne_della_tabella_ammesse(self, session):
        _repo(session, NewsEvent).select(where=[NewsEvent.ts >= _NOW])

        assert "t_news_events.ts >=" in _sql(session.execute.call_args.args[0])

    def test_colonna_sconosciuta_nel_where_rifiutata(self, session):
        with pytest.raises(InvalidQueryError, match="symbol"):
            _repo(session, NewsEvent).select(where={"symbol": "AAPL"})

    def test_sql_testuale_rifiutato(self, session):
        with pytest.raises(InvalidQueryError, match="SQL testuale"):
            _repo(session, NewsEvent).select(where=[text("1=1")])

    def test_sql_testuale_annidato_rifiutato(self, session):
        with pytest.raises(InvalidQueryError, match="SQL testuale"):
            _repo(session, NewsEvent).select(where=[NewsEvent.ts >= text("now()")])

    def test_colonna_di_altra_tabella_rifiutata(self, session):
        with pytest.raises(InvalidQueryError, match="t_assets"):
            _repo(session, NewsEvent).select(where=[Asset.symbol == "AAPL"])

    def test_colonna_non_legata_a_tabella_rifiutata(self, session):
        with pytest.raises(InvalidQueryError, match="headline"):
            _repo(session, NewsEvent).select(where=[column("headline") == "x"])

    def test_funzioni_sql_sulle_proprie_colonne_ammesse(self, session):
        _repo(session, NewsEvent).select(where=[func.lower(NewsEvent.headline) == "x"])

        session.execute.assert_called_once()


class TestScrittureSenzaFiltro:
    def test_delete_senza_where_rifiutato(self, session):
        with pytest.raises(InvalidQueryError, match="where"):
            _repo(session).delete(where={})
        session.execute.assert_not_called()

    def test_update_senza_where_rifiutato(self, session):
        with pytest.raises(InvalidQueryError, match="where"):
            _repo(session).update({"close": 1.0}, where=[])

    def test_update_non_puo_modificare_la_primary_key(self, session):
        with pytest.raises(InvalidQueryError, match="ts"):
            _repo(session).update({"ts": _NOW}, where={"asset_id": 1})

    def test_update_senza_valori_rifiutato(self, session):
        with pytest.raises(InvalidQueryError, match="nessun valore"):
            _repo(session).update({}, where={"asset_id": 1})


class TestLetture:
    def test_limite_di_default(self, session):
        _repo(session).select()

        sql = _sql(session.execute.call_args.args[0])
        assert "LIMIT" in sql
        assert session.execute.call_args.args[0]._limit == TableRepository.DEFAULT_READ_LIMIT

    def test_limit_none_esplicito_disattiva_il_limite(self, session):
        _repo(session).select(limit=None)

        assert "LIMIT" not in _sql(session.execute.call_args.args[0])

    def test_limit_non_positivo_rifiutato(self, session):
        with pytest.raises(InvalidQueryError, match="limit"):
            _repo(session).select(limit=0)

    def test_order_by_su_colonna_sconosciuta_rifiutato(self, session):
        with pytest.raises(InvalidQueryError, match="nope"):
            _repo(session).select(order_by=("nope",))

    def test_order_by_discendente_con_trattino(self, session):
        _repo(session).select(order_by=("-ts",))

        assert "ORDER BY market_data.t_market_prices.ts DESC" in _sql(
            session.execute.call_args.args[0]
        )

    def test_get_one_richiede_una_chiave_candidata_completa(self, session):
        with pytest.raises(InvalidQueryError, match="chiave"):
            _repo(session).get_one(asset_id=1, ts=_NOW)

    def test_get_one_su_chiave_unique(self, session):
        _repo(session, Asset).get_one(symbol="AAPL")

        session.execute.assert_called_once()

    def test_values_su_colonna_sconosciuta_rifiutato(self, session):
        with pytest.raises(InvalidQueryError, match="nope"):
            _repo(session, Asset).values("nope")


class TestLatestPer:
    def test_distinct_on_con_ordinamento_decrescente(self, session):
        _repo(session, MacroEvent).latest_per(("indicator",), order_by="ts", where=[MacroEvent.ts <= _NOW.date()])

        sql = _sql(session.execute.call_args.args[0])
        assert "DISTINCT ON (market_data.t_macro_events.indicator)" in sql
        assert "ORDER BY market_data.t_macro_events.indicator, market_data.t_macro_events.ts DESC" in sql
        assert "t_macro_events.ts <=" in sql

    def test_colonne_sconosciute_rifiutate(self, session):
        with pytest.raises(InvalidQueryError, match="nope"):
            _repo(session, MacroEvent).latest_per(("nope",), order_by="ts")
        with pytest.raises(InvalidQueryError, match="nope"):
            _repo(session, MacroEvent).latest_per(("indicator",), order_by="nope")

    def test_group_by_vuoto_rifiutato(self, session):
        with pytest.raises(InvalidQueryError, match="group_by"):
            _repo(session, MacroEvent).latest_per((), order_by="ts")
