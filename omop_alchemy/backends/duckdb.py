from __future__ import annotations

import sqlalchemy as sa
from oa_configurator import Dialect, Role, physical_schema_of, qualified

from .base import Backend, FeatureNotSupportedError


class DuckDBBackend(Backend):

    @property
    def name(self) -> str:
        return "DuckDB"

    @property
    def dialect(self) -> str:
        return Dialect.DUCKDB

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

        DuckDB's ``TRUNCATE`` does not accept a comma-separated table list
        (unlike Postgres), so each table is truncated individually within
        the caller's transaction. ``restart_identities`` is not supported:
        DuckDB has no ``RESTART IDENTITY`` clause and no way to reset a
        sequence already bound to a column default (``ALTER SEQUENCE ...
        RESTART WITH`` is unimplemented, and ``CREATE OR REPLACE SEQUENCE``
        is rejected while a column default still depends on it).
        """
        if restart_identities:
            raise FeatureNotSupportedError("TRUNCATE with RESTART IDENTITY", self)
        for schema_tag, table_name in tables:
            sql = f"TRUNCATE {qualified(conn, table_name, physical_schema=physical_schema_of(conn, schema_tag=schema_tag))}"
            if cascade:
                sql += " CASCADE"
            conn.exec_driver_sql(sql)

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
        """Anti-join count: DuckDB has no triggers to inspect, so this counts
        orphaned rows directly instead of reading trigger-enforced violation
        state (there is none to read)."""
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
