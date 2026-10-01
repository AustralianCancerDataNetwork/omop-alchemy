"""Schema-provenance guard wired into create_missing_tables(), install_fulltext_columns(),
manage_indexes(), and truncate_tables().

Live-Postgres regression: proves the guard actually fires and prevents DDL
for a genuinely reconfigured schema, rather than passing by coincidence.

One case per call site is covered here. The guard's own agree/no-op/test_only
semantics are already exhaustively covered at the primitive level in
oa-configurator's own test suite; what's worth proving per consuming repo
is that each call site is actually wired to it, and a wiring mistake (e.g.
a guard wrapping an empty `pass` instead of the real DDL) would show up here
too.

pg_engine (unlike pg_db) is a real, committing engine, so every provenance
row this file's tests write is a genuine commit. cleanup_after_test deletes
this test's own schema_provenance rows at teardown (see Phase 10.12 in the
plan).
"""

from __future__ import annotations

import dataclasses
import uuid

import pytest
import sqlalchemy as sa
from oa_configurator import SchemaDriftError
from oa_configurator.domains.resources.schema_registry import SchemaRegistry

from oa_configurator.testing import delete_rows_on_cleanup, isolated_test_schema

from omop_alchemy.backends import CONCEPT_NAME_TSVECTOR_COLUMN
from omop_alchemy.backends.base import FullTextError
from omop_alchemy.maintenance.cli_fulltext import install_fulltext_columns
from omop_alchemy.maintenance.cli_indexes import manage_indexes
from omop_alchemy.maintenance.cli_schema_tables import create_missing_tables
from omop_alchemy.maintenance.cli_tables import truncate_tables
from omop_alchemy.maintenance.tables import TableCategory

pytestmark = [pytest.mark.postgresql, pytest.mark.db_dialect]


def _resolved(pg_db, *, database_name: str, schema: str):
    """pg_db.resolved with a unique name (the guard's own key includes it),
    all three schemas pointed at schema, and both connection.test_only and
    vocab_connection.test_only forced False so the guard doesn't no-op
    against pg_db's own test-only marking. A VOCAB-tagged guard reads
    vocab_connection specifically (connection_for_role(Role.VOCAB)), so
    fixing only connection leaves it silently no-op'd.
    """
    return dataclasses.replace(
        pg_db.resolved,
        name=database_name,
        schema_name=schema,
        vocab_schema=schema,
        results_schema=schema,
        connection=dataclasses.replace(pg_db.resolved.connection, test_only=False),
        vocab_connection=dataclasses.replace(pg_db.resolved.vocab_connection, test_only=False),
    )


def test_create_missing_tables_guard_fires_on_reconfigured_schema(pg_db, pg_engine, cleanup_after_test):
    database_name = f"guard_wiring_test_db_{uuid.uuid4().hex[:8]}"
    table = SchemaRegistry.__table__
    delete_rows_on_cleanup(
        cleanup_after_test, pg_engine, table, table.c.database_name == database_name
    )
    with (
        isolated_test_schema(pg_engine, prefix="guard_wiring_a") as schema_a,
        isolated_test_schema(pg_engine, prefix="guard_wiring_b") as schema_b,
    ):
        resolved_a = _resolved(pg_db, database_name=database_name, schema=schema_a)
        engine_a = resolved_a.create_engine()
        create_missing_tables(engine_a, vocab_engine=engine_a, resolved=resolved_a)

        resolved_b = _resolved(pg_db, database_name=database_name, schema=schema_b)
        engine_b = resolved_b.create_engine()
        with pytest.raises(SchemaDriftError):
            create_missing_tables(engine_b, vocab_engine=engine_b, resolved=resolved_b)

        # The guard raises before create_all() runs: schema_b must still be empty.
        with engine_b.connect() as conn:
            assert sa.inspect(conn).get_table_names(schema=schema_b) == []


def test_install_fulltext_columns_guard_fires_on_reconfigured_schema(pg_db, pg_engine, cleanup_after_test):
    database_name = f"guard_wiring_test_db_{uuid.uuid4().hex[:8]}"
    table = SchemaRegistry.__table__
    delete_rows_on_cleanup(
        cleanup_after_test, pg_engine, table, table.c.database_name == database_name
    )
    with (
        isolated_test_schema(pg_engine, prefix="guard_wiring_ft_a") as schema_a,
        isolated_test_schema(pg_engine, prefix="guard_wiring_ft_b") as schema_b,
    ):
        resolved_a = _resolved(pg_db, database_name=database_name, schema=schema_a)
        engine_a = resolved_a.create_engine()
        # Guarded create establishes the provenance baseline for schema_a; an
        # unguarded create here would leave install_fulltext_columns's own guard
        # seeing "tables exist but no record", a false first-time-drift positive.
        create_missing_tables(engine_a, vocab_engine=engine_a, vocabulary_included=True, resolved=resolved_a)
        install_fulltext_columns(engine_a, resolved=resolved_a)

        resolved_b = _resolved(pg_db, database_name=database_name, schema=schema_b)
        engine_b = resolved_b.create_engine()
        # install_fulltext_columns wraps every underlying error, including the
        # guard's own SchemaDriftError, in its own FullTextError.
        with pytest.raises(FullTextError) as exc_info:
            install_fulltext_columns(engine_b, resolved=resolved_b)
        assert isinstance(exc_info.value.__cause__, SchemaDriftError)

        # The guard raises before any ALTER TABLE runs: schema_b's concept table
        # must not have picked up the tsvector sidecar column.
        with engine_b.connect() as conn:
            if sa.inspect(conn).has_table("concept", schema=schema_b):
                columns = {c["name"] for c in sa.inspect(conn).get_columns("concept", schema=schema_b)}
                assert CONCEPT_NAME_TSVECTOR_COLUMN not in columns


def test_manage_indexes_enable_guard_fires_on_reconfigured_schema(pg_db, pg_engine, cleanup_after_test):
    database_name = f"guard_wiring_test_db_{uuid.uuid4().hex[:8]}"
    table = SchemaRegistry.__table__
    delete_rows_on_cleanup(
        cleanup_after_test, pg_engine, table, table.c.database_name == database_name
    )
    with (
        isolated_test_schema(pg_engine, prefix="guard_wiring_idx_a") as schema_a,
        isolated_test_schema(pg_engine, prefix="guard_wiring_idx_b") as schema_b,
    ):
        resolved_a = _resolved(pg_db, database_name=database_name, schema=schema_a)
        engine_a = resolved_a.create_engine()
        create_missing_tables(engine_a, vocab_engine=engine_a, vocabulary_included=True, resolved=resolved_a)
        manage_indexes(engine_a, vocab_engine=engine_a, enable=True, cluster=False, resolved=resolved_a)

        resolved_b = _resolved(pg_db, database_name=database_name, schema=schema_b)
        engine_b = resolved_b.create_engine()
        with pytest.raises(SchemaDriftError):
            manage_indexes(
                engine_b, vocab_engine=engine_b,
                enable=True,
                cluster=False,
                resolved=resolved_b,
            )


def test_manage_indexes_disable_guard_fires_on_reconfigured_schema(pg_db, pg_engine, cleanup_after_test):
    """Regression for the disable path specifically: manage_indexes(enable=False)
    used to build no guard at all, regardless of resolved."""
    database_name = f"guard_wiring_test_db_{uuid.uuid4().hex[:8]}"
    table = SchemaRegistry.__table__
    delete_rows_on_cleanup(
        cleanup_after_test, pg_engine, table, table.c.database_name == database_name
    )
    with (
        isolated_test_schema(pg_engine, prefix="guard_wiring_idxd_a") as schema_a,
        isolated_test_schema(pg_engine, prefix="guard_wiring_idxd_b") as schema_b,
    ):
        resolved_a = _resolved(pg_db, database_name=database_name, schema=schema_a)
        engine_a = resolved_a.create_engine()
        create_missing_tables(engine_a, vocab_engine=engine_a, vocabulary_included=True, resolved=resolved_a)
        manage_indexes(engine_a, vocab_engine=engine_a, enable=False, resolved=resolved_a)

        resolved_b = _resolved(pg_db, database_name=database_name, schema=schema_b)
        engine_b = resolved_b.create_engine()
        # Registered under its own name first via create_engine(), so schema_b
        # has a real baseline before create_missing_tables runs below -- an
        # unregistered schema raises SchemaDriftError same as a real mismatch would.
        setup_resolved = dataclasses.replace(resolved_b, name=f"{database_name}_setup")
        delete_rows_on_cleanup(
            cleanup_after_test, pg_engine, table, table.c.database_name == setup_resolved.name
        )
        setup_resolved.create_engine().dispose()
        create_missing_tables(
            engine_b, vocab_engine=engine_b, vocabulary_included=True,
            resolved=setup_resolved,
        )
        with pytest.raises(SchemaDriftError):
            manage_indexes(
                engine_b, vocab_engine=engine_b,
                enable=False,
                resolved=resolved_b,
            )


def test_truncate_tables_guard_fires_on_reconfigured_schema(pg_db, pg_engine, cleanup_after_test):
    database_name = f"guard_wiring_test_db_{uuid.uuid4().hex[:8]}"
    table = SchemaRegistry.__table__
    delete_rows_on_cleanup(
        cleanup_after_test, pg_engine, table, table.c.database_name == database_name
    )
    with (
        isolated_test_schema(pg_engine, prefix="guard_wiring_trunc_a") as schema_a,
        isolated_test_schema(pg_engine, prefix="guard_wiring_trunc_b") as schema_b,
    ):
        resolved_a = _resolved(pg_db, database_name=database_name, schema=schema_a)
        engine_a = resolved_a.create_engine()
        create_missing_tables(engine_a, vocab_engine=engine_a, vocabulary_included=True, resolved=resolved_a)
        truncate_tables(engine_a, vocab_engine=engine_a, scope=TableCategory.VOCABULARY, cascade=True, resolved=resolved_a)

        resolved_b = _resolved(pg_db, database_name=database_name, schema=schema_b)
        engine_b = resolved_b.create_engine()
        # See test_manage_indexes_disable_guard_fires_on_reconfigured_schema:
        # guarded under an unrelated name, registered via create_engine()
        # first so that name has its own real baseline for schema_b.
        setup_resolved = dataclasses.replace(resolved_b, name=f"{database_name}_setup")
        delete_rows_on_cleanup(
            cleanup_after_test, pg_engine, table, table.c.database_name == setup_resolved.name
        )
        setup_resolved.create_engine().dispose()
        create_missing_tables(
            engine_b, vocab_engine=engine_b, vocabulary_included=True,
            resolved=setup_resolved,
        )
        with pytest.raises(SchemaDriftError):
            truncate_tables(
                engine_b, vocab_engine=engine_b,
                scope=TableCategory.VOCABULARY,
                cascade=True,
                resolved=resolved_b,
            )
