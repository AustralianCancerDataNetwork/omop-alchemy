from __future__ import annotations

import os
import shutil

import sqlalchemy as sa

from oa_configurator import qualified, schema_of
from sqlalchemy.dialects.postgresql import REGCONFIG, TSVECTOR
from sqlalchemy.sql import func

from .base import Backend, FullTextTargetConfig


class PostgresBackend(Backend):

    @property
    def name(self) -> str:
        return "PostgreSQL"

    @property
    def dialect(self) -> str:
        return "postgresql"

    # ── FK trigger management ────────────────────────────────────────────────

    def toggle_fk_triggers(
        self,
        conn: sa.Connection,
        table_name: str,
        *,
        enable: bool,
    ) -> None:
        action = "ENABLE" if enable else "DISABLE"
        conn.exec_driver_sql(
            f"ALTER TABLE {qualified(conn, table_name)} {action} TRIGGER ALL"
        )

    def get_fk_trigger_counts(
        self,
        conn: sa.Connection,
        table_name: str,
    ) -> tuple[int, int]:
        disabled_count, enabled_count = conn.execute(
            sa.text(
                """
                SELECT
                    SUM(CASE WHEN t.tgenabled = 'D' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN t.tgenabled <> 'D' THEN 1 ELSE 0 END)
                FROM pg_trigger t
                JOIN pg_class c ON c.oid = t.tgrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE t.tgisinternal
                  AND t.tgname LIKE 'RI_ConstraintTrigger%'
                  AND c.relname = :table_name
                  AND (CAST(:db_schema AS TEXT) IS NULL OR n.nspname = :db_schema)
                """
            ),
            {"table_name": table_name, "db_schema": schema_of(conn)},
        ).one()
        return int(disabled_count or 0), int(enabled_count or 0)

    def count_fk_violations(
        self,
        conn: sa.Connection,
        source_table: str,
        referred_table: str,
        constrained_cols: list[str],
        referred_cols: list[str],
    ) -> int:
        source = qualified(conn, source_table)
        referred = qualified(conn, referred_table)
        non_null_predicate = " AND ".join(
            f"src.{col} IS NOT NULL" for col in constrained_cols
        )
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

    # ── Clustering ───────────────────────────────────────────────────────────

    def cluster_table(
        self,
        conn: sa.Connection,
        table_name: str,
        index_name: str,
    ) -> None:
        conn.exec_driver_sql(
            f"CLUSTER {qualified(conn, table_name)} USING {index_name}"
        )

    def get_clustered_index_name(
        self,
        conn: sa.Connection,
        table_name: str,
    ) -> str | None:
        result = conn.execute(
            sa.text(
                """
                SELECT i.relname
                FROM pg_index ix
                JOIN pg_class t ON t.oid = ix.indrelid
                JOIN pg_class i ON i.oid = ix.indexrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE ix.indisclustered
                  AND t.relname = :table_name
                  AND (CAST(:db_schema AS TEXT) IS NULL OR n.nspname = :db_schema)
                """
            ),
            {"table_name": table_name, "db_schema": schema_of(conn)},
        ).scalar_one_or_none()
        return str(result) if result is not None else None

    # ── Table operations ─────────────────────────────────────────────────────

    def analyze_table(
        self,
        conn: sa.Connection,
        table_name: str,
        *,
        vacuum: bool = False,
    ) -> None:
        operation = "VACUUM ANALYZE" if vacuum else "ANALYZE"
        conn.exec_driver_sql(f"{operation} {qualified(conn, table_name)}")

    def index_exists(
        self,
        conn: sa.Connection,
        index_name: str,
    ) -> bool:
        qualified_index_name = qualified(conn, index_name)
        return bool(
            conn.scalar(
                sa.select(
                    sa.func.to_regclass(qualified_index_name).is_not(None)
                )
            )
        )

    def drop_index_if_exists(self, conn: sa.Connection, index_name: str) -> None:
        conn.exec_driver_sql(f"DROP INDEX IF EXISTS {qualified(conn, index_name)}")

    def truncate_table_batch(
        self,
        conn: sa.Connection,
        table_names: list[str],
        *,
        restart_identities: bool,
        cascade: bool,
    ) -> None:
        sql = "TRUNCATE TABLE " + ", ".join(
            qualified(conn, name) for name in table_names
        )
        if restart_identities:
            sql += " RESTART IDENTITY"
        if cascade:
            sql += " CASCADE"
        conn.exec_driver_sql(sql)

    # ── Sequence management ──────────────────────────────────────────────────

    def find_sequence_name(
        self,
        conn: sa.Connection,
        table_name: str,
        column_name: str,
    ) -> str | None:
        fully_qualified = qualified(conn, table_name)
        return conn.execute(
            sa.text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
            {"table_name": fully_qualified, "column_name": column_name},
        ).scalar_one_or_none()

    def set_sequence_value(
        self,
        conn: sa.Connection,
        sequence_name: str,
        value: int,
    ) -> None:
        conn.execute(
            sa.text("SELECT setval(:sequence_name, :value, false)"),
            {"sequence_name": sequence_name, "value": value},
        )

    # ── Full-text search ─────────────────────────────────────────────────────

    @property
    def fulltext_targets(self) -> tuple[FullTextTargetConfig, ...]:
        return (
            FullTextTargetConfig(
                table_name="concept",
                source_column_name="concept_name",
                vector_column_name="concept_name_tsvector",
                index_name="idx_gin_concept_name_tsvector",
            ),
            FullTextTargetConfig(
                table_name="concept_synonym",
                source_column_name="concept_synonym_name",
                vector_column_name="concept_synonym_name_tsvector",
                index_name="idx_gin_concept_synonym_name_tsvector",
            ),
        )

    def register_fulltext_metadata(self) -> None:
        from typing import cast
        from ..cdm.model.vocabulary.concept import Concept
        from ..cdm.model.vocabulary.concept_synonym import Concept_Synonym
        table_map = {
            "concept": cast(sa.Table, Concept.__table__),
            "concept_synonym": cast(sa.Table, Concept_Synonym.__table__),
        }
        for cfg in self.fulltext_targets:
            table = table_map[cfg.table_name]
            if cfg.vector_column_name not in table.c:
                table.append_column(sa.Column(cfg.vector_column_name, TSVECTOR, nullable=True))

    def unregister_fulltext_metadata(self) -> None:
        from typing import cast
        from ..cdm.model.vocabulary.concept import Concept
        from ..cdm.model.vocabulary.concept_synonym import Concept_Synonym
        table_map = {
            "concept": cast(sa.Table, Concept.__table__),
            "concept_synonym": cast(sa.Table, Concept_Synonym.__table__),
        }
        for cfg in self.fulltext_targets:
            table = table_map[cfg.table_name]
            column = table.c.get(cfg.vector_column_name)
            if column is not None:
                table._columns.remove(column)

    def concept_name_tsvector_expression(self, *, regconfig: str = "english") -> sa.ColumnElement:
        from typing import cast
        from ..cdm.model.vocabulary.concept import Concept
        col = cast(sa.Table, Concept.__table__).c.get("concept_name_tsvector")
        if col is not None:
            return col
        return func.to_tsvector(regconfig, func.coalesce(Concept.concept_name, ""))

    def concept_synonym_name_tsvector_expression(self, *, regconfig: str = "english") -> sa.ColumnElement:
        from typing import cast
        from ..cdm.model.vocabulary.concept_synonym import Concept_Synonym
        col = cast(sa.Table, Concept_Synonym.__table__).c.get("concept_synonym_name_tsvector")
        if col is not None:
            return col
        return func.to_tsvector(regconfig, func.coalesce(Concept_Synonym.concept_synonym_name, ""))

    def install_fulltext_on_table(
        self,
        conn: sa.Connection,
        *,
        table_name: str,
        vector_column_name: str,
        index_name: str,
        create_indexes: bool,
        fastupdate: bool,
    ) -> None:
        qualified_table = qualified(conn, table_name)
        conn.exec_driver_sql(
            f"ALTER TABLE {qualified_table} ADD COLUMN IF NOT EXISTS {vector_column_name} tsvector"
        )
        if create_indexes:
            lightweight_table = sa.Table(
                table_name,
                sa.MetaData(),
                sa.Column(vector_column_name, TSVECTOR),
                schema=schema_of(conn),
            )
            index = sa.Index(
                index_name,
                lightweight_table.c[vector_column_name],
                postgresql_using="gin",
                postgresql_with={"fastupdate": "on" if fastupdate else "off"},
            )
            conn.execute(sa.schema.CreateIndex(index, if_not_exists=True))

    def populate_fulltext_on_table(
        self,
        conn: sa.Connection,
        *,
        table_name: str,
        vector_column_name: str,
        source_column_name: str,
        regconfig: str,
    ) -> int | None:
        lightweight_table = sa.table(
            table_name,
            sa.column(vector_column_name),
            sa.column(source_column_name),
            schema=schema_of(conn),
        )
        source_column = lightweight_table.c[source_column_name]
        stmt = lightweight_table.update().values(
            **{
                vector_column_name: func.to_tsvector(
                    sa.cast(sa.bindparam("regconfig"), REGCONFIG),
                    func.coalesce(source_column, ""),
                )
            }
        )
        result = conn.execute(stmt, {"regconfig": regconfig})
        if result.rowcount is None or result.rowcount < 0:
            return None
        return int(result.rowcount)

    def drop_fulltext_on_table(
        self,
        conn: sa.Connection,
        *,
        table_name: str,
        vector_column_name: str,
        index_name: str,
        drop_indexes: bool,
    ) -> None:
        if drop_indexes:
            conn.exec_driver_sql(f"DROP INDEX IF EXISTS {qualified(conn, index_name)}")
        conn.exec_driver_sql(
            f"ALTER TABLE {qualified(conn, table_name)}"
            f" DROP COLUMN IF EXISTS {vector_column_name}"
        )

    # ── Backup / restore ─────────────────────────────────────────────────────

    def prepare_backup(
        self,
        engine: sa.Engine,
        output_path: str,
        backup_format: str,
    ) -> tuple[str, list[str], dict[str, str], str]:
        tool_path = _pg_dump_path()
        url = engine.url
        database_name = url.database
        if not database_name:
            raise RuntimeError(
                "Database backup requires a database name in the configured engine URL."
            )
        connection_uri = _libpq_connection_uri(url)
        command = [
            tool_path,
            "--format", backup_format,
            "--file", output_path,
            "--dbname", connection_uri,
            "--no-password",
            "--no-owner",
            "--no-privileges",
        ]
        db_schema = schema_of(engine)
        if db_schema:
            command.extend(["--schema", db_schema])
        env = os.environ.copy()
        if url.password:
            env["PGPASSWORD"] = str(url.password)
        return tool_path, command, env, database_name

    def prepare_restore(
        self,
        engine: sa.Engine,
        input_path: str,
        backup_format: str,
    ) -> tuple[str, list[str], dict[str, str], str]:
        url = engine.url
        database_name = url.database
        if not database_name:
            raise RuntimeError(
                "Database restore requires a database name in the configured engine URL."
            )
        connection_uri = _libpq_connection_uri(url)
        db_schema = schema_of(engine)

        if backup_format == "custom":
            tool_path = _pg_restore_path()
            command = [
                tool_path,
                "--dbname", connection_uri,
                "--no-password",
                "--no-owner",
                "--no-privileges",
                "--exit-on-error",
            ]
            if db_schema:
                command.extend(["--schema", db_schema])
            command.append(input_path)
        else:
            tool_path = _psql_path()
            command = [
                tool_path,
                "--dbname", connection_uri,
                "--no-password",
                "--set", "ON_ERROR_STOP=1",
                "--single-transaction",
                "--file", input_path,
            ]

        env = os.environ.copy()
        if url.password:
            env["PGPASSWORD"] = str(url.password)
        return tool_path, command, env, database_name


# ── subprocess tool helpers ───────────────────────────────────────────────────

def _pg_dump_path() -> str:
    tool_path = shutil.which("pg_dump")
    if tool_path is None:
        raise RuntimeError(
            "The `pg_dump` executable is required for database backups but was not found on PATH. "
            "Install PostgreSQL client tools and ensure `pg_dump` is available."
        )
    return tool_path


def _pg_restore_path() -> str:
    tool_path = shutil.which("pg_restore")
    if tool_path is None:
        raise RuntimeError(
            "The `pg_restore` executable is required to restore custom PostgreSQL dumps "
            "but was not found on PATH. "
            "Install PostgreSQL client tools and ensure `pg_restore` is available."
        )
    return tool_path


def _psql_path() -> str:
    tool_path = shutil.which("psql")
    if tool_path is None:
        raise RuntimeError(
            "The `psql` executable is required to restore plain SQL PostgreSQL dumps "
            "but was not found on PATH. "
            "Install PostgreSQL client tools and ensure `psql` is available."
        )
    return tool_path


def _libpq_connection_uri(url: sa.engine.URL) -> str:
    if not url.database:
        raise RuntimeError(
            "Database backup requires a database name in the configured engine URL."
        )
    libpq_url = sa.engine.URL.create(
        drivername="postgresql",
        username=url.username,
        password=None,
        host=url.host,
        port=url.port,
        database=url.database,
        query=url.query,
    )
    return libpq_url.render_as_string(hide_password=False)
