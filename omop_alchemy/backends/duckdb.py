from __future__ import annotations

import sqlalchemy as sa
from oa_configurator import Dialect, Role, physical_schema_of, qualified

from .base import Backend, FeatureNotSupportedError


def _truncate_order(conn: sa.Connection, tables: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """tables, reordered so each is truncated before anything it has an
    enforced FK to, regardless of the caller's input order.

    Raises
    ------
    RuntimeError
        If an FK cycle is reflected among the given tables anyway (would
        mean a table's own use_alter=True edges are wrong, not a case this
        function should silently paper over).
    """
    by_name = {table_name: (schema_tag, table_name) for schema_tag, table_name in tables}
    # dependents[T]: tables in the given set that FK-reference T, i.e. must
    # be truncated *before* T (truncating T first would violate their FK).
    dependents: dict[str, set[str]] = {name: set() for name in by_name}
    for schema_tag, table_name in tables:
        physical_schema = physical_schema_of(conn, schema_tag=schema_tag)
        for foreign_key in sa.inspect(conn).get_foreign_keys(table_name, schema=physical_schema):
            referred_table = foreign_key.get("referred_table")
            if referred_table in by_name and referred_table != table_name:
                dependents[str(referred_table)].add(table_name)

    ordered: list[str] = []
    remaining = set(by_name)
    while remaining:
        ready = sorted(name for name in remaining if not (dependents[name] & remaining))
        if not ready:
            raise RuntimeError(
                f"Cannot order truncation for {sorted(remaining)}: unexpected FK cycle among enforced constraints."
            )
        ordered.extend(ready)
        remaining -= set(ready)
    return [by_name[name] for name in ordered]


def _split_schema_qualified(name: str) -> tuple[str | None, str]:
    """(schema, bare_name) from a possibly dotted identifier, as returned by
    DuckDBBackend.find_sequence_name."""
    if "." in name:
        schema, _, bare = name.partition(".")
        return schema, bare
    return None, name


def _sequence_backed_columns(conn: sa.Connection, table_name: str, *, schema_tag: str = Role.PRIMARY.value) -> list[str]:
    """Names of table_name's columns whose DEFAULT is a nextval() call."""
    return list(
        conn.execute(
            sa.text(
                """
                SELECT column_name
                FROM duckdb_columns()
                WHERE table_name = :table_name
                  AND schema_name = :db_schema
                  AND column_default LIKE 'nextval(%'
                """
            ),
            {"table_name": table_name, "db_schema": physical_schema_of(conn, schema_tag=schema_tag)},
        ).scalars()
    )


def _sequence_start_value(conn: sa.Connection, sequence_name: str, *, schema_tag: str = Role.PRIMARY.value) -> int:
    """The START value sequence_name (bare or schema-qualified) was created with."""
    physical_schema, bare_name = _split_schema_qualified(sequence_name)
    return conn.execute(
        sa.text("SELECT start_value FROM duckdb_sequences() WHERE sequence_name = :name AND schema_name = :db_schema"),
        {"name": bare_name, "db_schema": physical_schema or physical_schema_of(conn, schema_tag=schema_tag)},
    ).scalar_one()


class DuckDBBackend(Backend):

    @property
    def name(self) -> str:
        return "DuckDB"

    @property
    def dialect(self) -> str:
        return Dialect.DUCKDB

    # ── Schema reconciliation ────────────────────────────────────────────────

    def normalize_index_expression(self, sql_text: str) -> str:
        """Strip the string-literal wrapper, redundant outer parens, and
        identifier quoting DuckDB's own catalog adds when reflecting a
        functional index's expression back, so the result compares equal to
        the ORM's own compiled expression text.

        Verified live: an index on ``lower(name)`` reflects back as the
        literal text ``'(lower("name"))'`` -- wrapped in single quotes (part
        of the string's own content, not Python repr), wrapped in one extra
        pair of parens, and with the identifier double-quoted.
        """
        text = sql_text.strip()
        if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
            text = text[1:-1]
        if len(text) >= 2 and text[0] == "(" and text[-1] == ")":
            text = text[1:-1]
        return text.replace('"', "").replace(" ", "").lower()

    # ── Table operations ─────────────────────────────────────────────────────

    def analyze_table(
        self,
        conn: sa.Connection,
        table_name: str,
        *,
        vacuum: bool = False,
        schema_tag: str = Role.PRIMARY.value,
    ) -> None:
        operation = "VACUUM ANALYZE" if vacuum else "ANALYZE"
        conn.exec_driver_sql(
            f"{operation} {qualified(conn, table_name, physical_schema=physical_schema_of(conn, schema_tag=schema_tag))}"
        )

    def index_exists(
        self,
        conn: sa.Connection,
        index_name: str,
        *,
        schema_tag: str = Role.PRIMARY.value,
    ) -> bool:
        row = conn.execute(
            sa.text(
                "SELECT 1 FROM duckdb_indexes() WHERE index_name = :index_name AND schema_name = :db_schema"
            ),
            {"index_name": index_name, "db_schema": physical_schema_of(conn, schema_tag=schema_tag)},
        ).fetchone()
        return row is not None

    def drop_index_if_exists(
        self, conn: sa.Connection, index_name: str, *, schema_tag: str = Role.PRIMARY.value
    ) -> None:
        conn.exec_driver_sql(
            f"DROP INDEX IF EXISTS {qualified(conn, index_name, physical_schema=physical_schema_of(conn, schema_tag=schema_tag))}"
        )

    def truncate_table_batch(
        self,
        conn: sa.Connection,
        tables: list[tuple[str, str]],
        *,
        restart_identities: bool,
        cascade: bool,
    ) -> None:
        """Truncate every (schema_tag, table_name) pair, one statement per table.

        Notes
        -----
        - DuckDB's ``TRUNCATE`` does not accept a comma-separated table list.
        Each table is truncated individually, ordered so a table is always
        truncated before anything it has an enforced FK to (see _truncate_order()),

        - The truncation is then committed mid-stream, requiring a connection 
        from ``engine.connect()`` rather than ``engine.begin()``. 

        - ``restart_identities`` resets every sequence-backed
        column on each truncated table to that sequence's own defined start
        value, since TRUNCATE itself never touches a sequence's current
        value

        - ``cascade`` is not supported as DuckDB's parser silently ignores it.
        The caller should expand the table selection to include the real dependents instead.
        """
        if cascade:
            raise FeatureNotSupportedError("TRUNCATE with CASCADE", self)
        ordered = _truncate_order(conn, tables)
        for schema_tag, table_name in ordered:
            sql = f"TRUNCATE {qualified(conn, table_name, physical_schema=physical_schema_of(conn, schema_tag=schema_tag))}"
            conn.exec_driver_sql(sql)
            conn.commit()
        if restart_identities:
            for schema_tag, table_name in ordered:
                for column_name in _sequence_backed_columns(conn, table_name, schema_tag=schema_tag):
                    sequence_name = self.find_sequence_name(conn, table_name, column_name, schema_tag=schema_tag)
                    if sequence_name is None:
                        continue
                    start_value = _sequence_start_value(conn, sequence_name, schema_tag=schema_tag)
                    self.set_sequence_value(
                        conn, sequence_name, start_value, table_name=table_name, column_name=column_name
                    )

    # ── Sequence management ──────────────────────────────────────────────────

    def find_sequence_name(
        self,
        conn: sa.Connection,
        table_name: str,
        column_name: str,
        *,
        schema_tag: str = Role.PRIMARY.value,
    ) -> str | None:
        result = conn.execute(
            sa.text(
                """
                SELECT column_default
                FROM duckdb_columns()
                WHERE table_name = :table_name
                  AND column_name = :column_name
                  AND schema_name = :db_schema
                """
            ),
            {
                "table_name": table_name,
                "column_name": column_name,
                "db_schema": physical_schema_of(conn, schema_tag=schema_tag),
            },
        ).scalar_one_or_none()
        if result is None or not result.startswith("nextval("):
            return None
        return result[len("nextval('") : -2]

    def set_sequence_value(
        self,
        conn: sa.Connection,
        sequence_name: str,
        value: int,
        *,
        table_name: str,
        column_name: str,
    ) -> None:
        """Repoint table_name.column_name's DEFAULT so sequence_name's next
        value is value.

        Notes
        -----
        DuckDB has no ``ALTER SEQUENCE ... RESTART WITH`` and rejects
        ``CREATE OR REPLACE SEQUENCE`` while a column default still depends
        on the sequence being replaced. Therefore, sequence_name is swapped out
        by repointing the column default at a fresh temporary sequence.
        """
        physical_schema, bare_name = _split_schema_qualified(sequence_name)
        qualified_table = qualified(conn, table_name, physical_schema=physical_schema)
        tmp_name = f"__tmp_{bare_name}_reset"

        def nextval_ref(name: str) -> str:
            return f"{physical_schema}.{name}" if physical_schema else name

        conn.exec_driver_sql(f"CREATE SEQUENCE {qualified(conn, tmp_name, physical_schema=physical_schema)} START {value}")
        conn.exec_driver_sql(
            f"ALTER TABLE {qualified_table} ALTER COLUMN {column_name} SET DEFAULT nextval('{nextval_ref(tmp_name)}')"
        )
        conn.exec_driver_sql(f"DROP SEQUENCE {qualified(conn, bare_name, physical_schema=physical_schema)}")
        conn.exec_driver_sql(f"CREATE SEQUENCE {qualified(conn, bare_name, physical_schema=physical_schema)} START {value}")
        conn.exec_driver_sql(
            f"ALTER TABLE {qualified_table} ALTER COLUMN {column_name} SET DEFAULT nextval('{nextval_ref(bare_name)}')"
        )
        conn.exec_driver_sql(f"DROP SEQUENCE {qualified(conn, tmp_name, physical_schema=physical_schema)}")

    # ── FK violation counting (no triggers needed) ───────────────────────────

    def count_fk_violations(
        self,
        conn: sa.Connection,
        source_table: str,
        referred_table: str,
        constrained_cols: list[str],
        referred_cols: list[str],
        *,
        source_schema_tag: str = Role.PRIMARY.value,
        referred_schema_tag: str = Role.PRIMARY.value,
    ) -> int:
        """Anti-join count to count orphaned rows in source_table that violate a foreign key to referred_table.

        Notes
        -----
        No triggers to inspect in DuckDB, requiring the anti-join query.
        """
        source = qualified(conn, source_table, physical_schema=physical_schema_of(conn, schema_tag=source_schema_tag))
        referred = qualified(conn, referred_table, physical_schema=physical_schema_of(conn, schema_tag=referred_schema_tag))
        non_null_predicate = " AND ".join(f"src.{col} IS NOT NULL" for col in constrained_cols)
        join_predicate = " AND ".join(
            f"ref.{ref_col} = src.{src_col}"
            for src_col, ref_col in zip(constrained_cols, referred_cols, strict=True)
        )
        return int(
            conn.exec_driver_sql(
                f"""
                SELECT COUNT(*)
                FROM {source} AS src
                WHERE {non_null_predicate}
                  AND NOT EXISTS (
                      SELECT 1
                      FROM {referred} AS ref
                      WHERE {join_predicate}
                  )
                """
            ).scalar_one()
        )
