"""Repository generico per tabella, con i safeguard applicati in un punto solo.

`TableRepository` è l'unico modo in cui il codice applicativo nuovo legge e
scrive su Postgres: funzioni parametriche (`insert`, `upsert`, `select`,
`update`, `delete`, ...) costruite dai metadati del modello ORM, mai SQL
scritto a mano dal chiamante. I controlli avvengono prima di eseguire
qualunque statement:

- scrittura ammessa solo sugli schema della `AccessPolicy` del chiamante;
- colonne inesistenti rifiutate, colonne obbligatorie (NOT NULL senza
  default) richieste, tutte le righe di un batch con le stesse colonne;
- `conflict_on` di un upsert deve coincidere con la primary key o con un
  vincolo di unicità reale della tabella, e l'upsert non può aggiornare né
  la chiave di conflitto né la primary key né colonne non fornite (le
  imposterebbe a NULL/default sulla riga esistente);
- `UPDATE`/`DELETE` richiedono un filtro non vuoto, che può riferirsi solo
  a colonne della tabella stessa e non può contenere SQL testuale;
- letture con un limite di default, scritture divise in blocchi entro il
  massimo di parametri per statement di Postgres, batch vuoto = no-op.

Il repository lavora su una `Session` già aperta (tipicamente da
`Database.transaction()`), così più repository condividono la stessa
transazione — es. tabella raffinata e payload grezzo scritti insieme.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import Table, UniqueConstraint, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement, visitors
from sqlalchemy.sql.elements import ColumnClause, TextClause

from marketmind_db.access import AccessPolicy
from marketmind_db.base import Base
from marketmind_db.exceptions import InvalidQueryError

ModelT = TypeVar("ModelT", bound=Base)

Row = Mapping[str, Any]
# Filtro: uguaglianze per nome colonna (`{"source": "FMP"}`; una lista/tupla/
# set diventa `IN`, `None` diventa `IS NULL`) oppure espressioni SQLAlchemy
# sulle colonne del modello (`[MarketPrice.ts >= since]`).
Where = Mapping[str, Any] | Sequence[ColumnElement[bool]]

_SEQUENCE_TYPES = (list, tuple, set, frozenset)


class TableRepository(Generic[ModelT]):
    DEFAULT_READ_LIMIT = 1_000
    DEFAULT_BATCH_SIZE = 1_000
    # Postgres accetta al massimo 65.535 bind parameter per statement: un
    # INSERT multi-riga ne usa uno per cella.
    MAX_BIND_PARAMS = 65_535

    def __init__(
        self,
        session: Session,
        model: type[ModelT],
        policy: AccessPolicy,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        if batch_size < 1:
            raise InvalidQueryError(f"batch_size deve essere positivo, ricevuto {batch_size}")
        self.session = session
        self.model = model
        self.policy = policy
        self.batch_size = batch_size
        self.table: Table = model.__table__
        self.columns: frozenset[str] = frozenset(self.table.columns.keys())
        self.primary_key: frozenset[str] = frozenset(c.name for c in self.table.primary_key.columns)
        self.candidate_keys: frozenset[frozenset[str]] = _candidate_keys(self.table)
        self.required_columns: frozenset[str] = _required_columns(self.table)

    # --- scritture -------------------------------------------------------

    def insert(self, rows: Iterable[Row]) -> int:
        """`INSERT` semplice; restituisce il numero di righe scritte."""
        self.policy.check_writable(self.table)
        prepared, columns = self._prepare_rows(rows)
        written = 0
        for chunk in self._chunks(prepared, columns):
            written += self._count_written(pg_insert(self.table).values(chunk))
        return written

    def insert_returning(self, rows: Iterable[Row], *, returning: Sequence[str]) -> list[dict[str, Any]]:
        """Come `insert`, restituendo le colonne `returning` delle righe scritte
        (es. la primary key generata)."""
        self.policy.check_writable(self.table)
        returned = self._columns_by_name(returning, "returning")
        prepared, columns = self._prepare_rows(rows)
        result: list[dict[str, Any]] = []
        for chunk in self._chunks(prepared, columns):
            stmt = pg_insert(self.table).values(chunk).returning(*returned)
            result.extend(dict(r) for r in self.session.execute(stmt).mappings())
        return result

    def upsert(
        self,
        rows: Iterable[Row],
        *,
        conflict_on: Sequence[str],
        update: Sequence[str] | None = None,
    ) -> int:
        """`INSERT ... ON CONFLICT (<conflict_on>) DO UPDATE`.

        `update=None` aggiorna tutte le colonne fornite tranne chiave di
        conflitto e primary key; una sequenza esplicita aggiorna solo
        quelle; `update=()` diventa `DO NOTHING`. Righe duplicate sulla
        chiave nello stesso batch: vince l'ultima (Postgres rifiuterebbe lo
        statement intero). Restituisce le righe inserite o aggiornate.
        """
        written = 0
        for stmt in self._upsert_statements(rows, conflict_on, update):
            written += self._count_written(stmt)
        return written

    def upsert_returning(
        self,
        rows: Iterable[Row],
        *,
        conflict_on: Sequence[str],
        returning: Sequence[str],
        update: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Come `upsert`, restituendo le colonne `returning` di ogni riga
        inserita o aggiornata. Non ammette `DO NOTHING`: le righe in
        conflitto non verrebbero restituite."""
        returned = self._columns_by_name(returning, "returning")
        result: list[dict[str, Any]] = []
        for stmt in self._upsert_statements(rows, conflict_on, update, returning_required=True):
            result.extend(dict(r) for r in self.session.execute(stmt.returning(*returned)).mappings())
        return result

    def update(self, values: Row, *, where: Where) -> int:
        """`UPDATE` con filtro obbligatorio; la primary key non è modificabile."""
        self.policy.check_writable(self.table)
        if not values:
            raise InvalidQueryError(f"update su {self.table.fullname}: nessun valore da impostare")
        self._check_known(values.keys(), "update")
        touched_pk = sorted(self.primary_key & set(values))
        if touched_pk:
            raise InvalidQueryError(
                f"update su {self.table.fullname}: la primary key non è modificabile ({', '.join(touched_pk)})"
            )
        clauses = self._required_where(where, "update")
        stmt = update(self.table).where(*clauses).values(dict(values))
        return self.session.execute(stmt).rowcount

    def delete(self, *, where: Where) -> int:
        """`DELETE` con filtro obbligatorio."""
        self.policy.check_writable(self.table)
        clauses = self._required_where(where, "delete")
        return self.session.execute(delete(self.table).where(*clauses)).rowcount

    # --- letture ---------------------------------------------------------

    def select(
        self,
        *,
        where: Where | None = None,
        order_by: Sequence[str] = (),
        limit: int | None = DEFAULT_READ_LIMIT,
    ) -> list[ModelT]:
        """Istanze ORM che soddisfano `where`. `order_by` accetta nomi di
        colonna, con `-` davanti per l'ordine decrescente. `limit=None`
        (esplicito) disattiva il limite di default."""
        stmt = select(self.model).where(*self._where_clauses(where))
        stmt = self._apply_order_and_limit(stmt, order_by, limit)
        # `populate_existing`: dopo un `update` Core nella stessa sessione
        # l'identity map conterrebbe istanze con i valori vecchi.
        stmt = stmt.execution_options(populate_existing=True)
        return list(self.session.execute(stmt).scalars())

    def values(
        self,
        column: str,
        *,
        where: Where | None = None,
        order_by: Sequence[str] = (),
        limit: int | None = DEFAULT_READ_LIMIT,
        distinct: bool = False,
    ) -> list[Any]:
        """I valori di una sola colonna (es. tutti i `symbol`)."""
        [col] = self._columns_by_name((column,), "values")
        stmt = select(col).where(*self._where_clauses(where))
        if distinct:
            stmt = stmt.distinct()
        stmt = self._apply_order_and_limit(stmt, order_by, limit)
        return list(self.session.execute(stmt).scalars())

    def latest_per(
        self,
        group_by: Sequence[str],
        *,
        order_by: str,
        where: Where | None = None,
        limit: int | None = DEFAULT_READ_LIMIT,
    ) -> list[ModelT]:
        """Per ogni combinazione distinta di `group_by`, la riga col valore più
        alto di `order_by` tra quelle che soddisfano `where` — es. l'ultimo
        valore noto per indicatore macro fino a una data. `DISTINCT ON` di
        Postgres, una sola query."""
        group_by = (group_by,) if isinstance(group_by, str) else tuple(group_by)
        if not group_by:
            raise InvalidQueryError(f"latest_per su {self.table.fullname}: group_by vuoto")
        group_cols = self._columns_by_name(group_by, "group_by")
        [order_col] = self._columns_by_name((order_by,), "order_by")
        stmt = (
            select(self.model)
            .where(*self._where_clauses(where))
            .distinct(*group_cols)
            .order_by(*group_cols, order_col.desc())
            .execution_options(populate_existing=True)
        )
        stmt = self._apply_order_and_limit(stmt, (), limit)
        return list(self.session.execute(stmt).scalars())

    def get_one(self, **key: Any) -> ModelT | None:
        """La riga identificata da una chiave candidata completa (primary key
        o vincolo di unicità), `None` se non esiste."""
        if frozenset(key) not in self.candidate_keys:
            known = " | ".join("(" + ", ".join(sorted(k)) + ")" for k in sorted(self.candidate_keys, key=sorted))
            raise InvalidQueryError(
                f"get_one su {self.table.fullname}: ({', '.join(sorted(key))}) non è una chiave "
                f"candidata; chiavi disponibili: {known}"
            )
        stmt = (
            select(self.model)
            .where(*self._where_clauses(key))
            .execution_options(populate_existing=True)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def count(self, *, where: Where | None = None) -> int:
        stmt = select(func.count()).select_from(self.table).where(*self._where_clauses(where))
        return self.session.execute(stmt).scalar_one()

    # --- validazione e costruzione statement ------------------------------

    def _count_written(self, stmt: Any) -> int:
        """Righe scritte da un INSERT, contate dal `RETURNING` della primary
        key invece che da `rowcount`: su una hypertable TimescaleDB
        (`t_market_prices`) `INSERT ... ON CONFLICT` restituisce
        `rowcount = -1`. Con `DO NOTHING` le righe in conflitto non sono
        restituite, quindi il conteggio resta quello delle righe davvero
        scritte."""
        pk = [self.table.c[name] for name in sorted(self.primary_key)]
        return len(self.session.execute(stmt.returning(*pk)).all())

    def _prepare_rows(self, rows: Iterable[Row]) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
        prepared = [dict(row) for row in rows]
        if not prepared:
            return [], ()
        columns = tuple(prepared[0])
        expected = set(columns)
        for i, row in enumerate(prepared[1:], start=1):
            if set(row) != expected:
                raise InvalidQueryError(
                    f"{self.table.fullname}: tutte le righe di un batch devono avere le stesse "
                    f"colonne (riga 0: {sorted(expected)}, riga {i}: {sorted(row)})"
                )
        self._check_known(columns, "righe")
        missing = sorted(self.required_columns - expected)
        if missing:
            raise InvalidQueryError(
                f"{self.table.fullname}: colonne obbligatorie mancanti: {', '.join(missing)}"
            )
        return prepared, columns

    def _upsert_statements(
        self,
        rows: Iterable[Row],
        conflict_on: Sequence[str],
        update: Sequence[str] | None,
        *,
        returning_required: bool = False,
    ) -> Iterator[Any]:
        self.policy.check_writable(self.table)
        conflict_on = tuple(conflict_on)
        if frozenset(conflict_on) not in self.candidate_keys:
            raise InvalidQueryError(
                f"upsert su {self.table.fullname}: conflict_on=({', '.join(conflict_on)}) non "
                "corrisponde alla primary key né a un vincolo di unicità della tabella"
            )
        prepared, columns = self._prepare_rows(rows)
        if not prepared:
            return
        missing_key = sorted(set(conflict_on) - set(columns))
        if missing_key:
            raise InvalidQueryError(
                f"upsert su {self.table.fullname}: le righe non contengono le colonne di "
                f"conflict_on {', '.join(missing_key)}"
            )
        update_columns = self._update_columns(update, conflict_on, columns)
        if returning_required and not update_columns:
            raise InvalidQueryError(
                f"upsert_returning su {self.table.fullname}: con DO NOTHING le righe in "
                "conflitto non sarebbero restituite da returning"
            )

        deduplicated = list({tuple(r[k] for k in conflict_on): r for r in prepared}.values())
        for chunk in self._chunks(deduplicated, columns):
            stmt = pg_insert(self.table).values(chunk)
            if update_columns:
                stmt = stmt.on_conflict_do_update(
                    index_elements=list(conflict_on),
                    set_={c: stmt.excluded[c] for c in update_columns},
                )
            else:
                stmt = stmt.on_conflict_do_nothing(index_elements=list(conflict_on))
            yield stmt

    def _update_columns(
        self, update: Sequence[str] | None, conflict_on: tuple[str, ...], columns: tuple[str, ...]
    ) -> tuple[str, ...]:
        protected = set(conflict_on) | self.primary_key
        if update is None:
            return tuple(c for c in columns if c not in protected)
        update = tuple(update)
        self._check_known(update, "update")
        for name in update:
            if name in conflict_on:
                raise InvalidQueryError(
                    f"upsert su {self.table.fullname}: {name} è nella chiave di conflitto, non aggiornabile"
                )
            if name in self.primary_key:
                raise InvalidQueryError(
                    f"upsert su {self.table.fullname}: {name} è nella primary key, non aggiornabile"
                )
            if name not in columns:
                raise InvalidQueryError(
                    f"upsert su {self.table.fullname}: {name} non è tra le colonne fornite — "
                    "aggiornarla la imposterebbe a NULL/default sulla riga esistente"
                )
        return update

    def _chunks(self, rows: list[dict[str, Any]], columns: tuple[str, ...]) -> Iterator[list[dict[str, Any]]]:
        size = max(1, min(self.batch_size, self.MAX_BIND_PARAMS // max(1, len(columns))))
        for start in range(0, len(rows), size):
            yield rows[start : start + size]

    def _check_known(self, names: Iterable[str], context: str) -> None:
        unknown = sorted(set(names) - self.columns)
        if unknown:
            raise InvalidQueryError(
                f"{self.table.fullname}: colonne inesistenti in {context}: {', '.join(unknown)}"
            )

    def _columns_by_name(self, names: Sequence[str], context: str) -> list[Any]:
        names = (names,) if isinstance(names, str) else tuple(names)
        self._check_known(names, context)
        return [self.table.c[name] for name in names]

    def _where_clauses(self, where: Where | None) -> list[ColumnElement[bool]]:
        if where is None:
            return []
        if isinstance(where, Mapping):
            self._check_known(where.keys(), "where")
            clauses = []
            for name, value in where.items():
                col = self.table.c[name]
                if value is None:
                    clauses.append(col.is_(None))
                elif isinstance(value, _SEQUENCE_TYPES):
                    clauses.append(col.in_(list(value)))
                else:
                    clauses.append(col == value)
            return clauses
        if isinstance(where, str):
            raise InvalidQueryError(f"{self.table.fullname}: SQL testuale non ammesso nel where")
        clauses = list(where)
        for clause in clauses:
            self._check_expression(clause)
        return clauses

    def _required_where(self, where: Where, operation: str) -> list[ColumnElement[bool]]:
        clauses = self._where_clauses(where)
        if not clauses:
            raise InvalidQueryError(
                f"{operation} su {self.table.fullname} senza where: operazione sull'intera tabella non ammessa"
            )
        return clauses

    def _check_expression(self, clause: Any) -> None:
        for element in visitors.iterate(clause):
            if isinstance(element, TextClause):
                raise InvalidQueryError(f"{self.table.fullname}: SQL testuale non ammesso nel where")
            if isinstance(element, ColumnClause):
                table = element.table
                if table is None:
                    raise InvalidQueryError(
                        f"{self.table.fullname}: colonna non legata a una tabella nel where: {element.name}"
                    )
                if getattr(table, "fullname", None) != self.table.fullname:
                    raise InvalidQueryError(
                        f"{self.table.fullname}: il where può filtrare solo le proprie colonne, "
                        f"non {getattr(table, 'name', table)}.{element.name}"
                    )
        if not isinstance(clause, ColumnElement):
            raise InvalidQueryError(
                f"{self.table.fullname}: elemento del where non valido: {clause!r}"
            )

    def _apply_order_and_limit(self, stmt: Any, order_by: Sequence[str], limit: int | None) -> Any:
        if isinstance(order_by, str):
            order_by = (order_by,)
        for spec in order_by:
            descending = spec.startswith("-")
            [col] = self._columns_by_name((spec.removeprefix("-"),), "order_by")
            stmt = stmt.order_by(col.desc() if descending else col)
        if limit is not None:
            if limit < 1:
                raise InvalidQueryError(f"limit deve essere positivo o None, ricevuto {limit}")
            stmt = stmt.limit(limit)
        return stmt


def _candidate_keys(table: Table) -> frozenset[frozenset[str]]:
    """Insiemi di colonne utilizzabili come bersaglio di `ON CONFLICT` e come
    identificativo di una riga: primary key, vincoli `UNIQUE`, indici unici
    non parziali (un indice parziale richiederebbe anche il suo predicato)."""
    keys = {frozenset(c.name for c in table.primary_key.columns)}
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            keys.add(frozenset(c.name for c in constraint.columns))
    for index in table.indexes:
        if index.unique and index.dialect_options["postgresql"].get("where") is None:
            names = [c.name for c in index.columns]
            if len(names) == len(index.expressions):
                keys.add(frozenset(names))
    for col in table.columns:
        if col.unique:
            keys.add(frozenset({col.name}))
    return frozenset(k for k in keys if k)


def _required_columns(table: Table) -> frozenset[str]:
    """Colonne NOT NULL senza default lato client o server, esclusa la
    colonna autoincrement (la PK generata da Postgres)."""
    autoincrement = table.autoincrement_column
    return frozenset(
        col.name
        for col in table.columns
        if not col.nullable
        and col.default is None
        and col.server_default is None
        and col.identity is None
        and col is not autoincrement
    )
