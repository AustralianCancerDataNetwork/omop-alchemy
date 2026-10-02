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

scoped_test_schema() commits real schemas and registry rows. The Role-tag
rows are reset around each test.
"""

from __future__ import annotations

from contextlib import AbstractContextManager

import pytest
import sqlalchemy as sa
from oa_configurator import Role, SchemaDriftError
from oa_configurator.testing import (
    ScopedTestSchema,
    guarded_resolver,
    reset_schema_registry_rows,
    resolve_with_role_schemas,
    scoped_test_schema,
)

from omop_alchemy.backends import CONCEPT_NAME_TSVECTOR_COLUMN
from omop_alchemy.backends.base import FullTextError
from omop_alchemy.maintenance.cli_fulltext import install_fulltext_columns
from omop_alchemy.maintenance.cli_indexes import manage_indexes
from omop_alchemy.maintenance.cli_schema_tables import create_missing_tables
from omop_alchemy.maintenance.cli_tables import truncate_tables
from omop_alchemy.maintenance.tables import TableCategory

pytestmark = [pytest.mark.postgresql, pytest.mark.db_dialect]


@pytest.fixture(autouse=True)
def _fresh_role_rows(pg_db, cleanup_after_test):
    reset_schema_registry_rows(cleanup_after_test, pg_db.committing_engine)


def _guarded_schema(pg_db, prefix: str) -> AbstractContextManager[ScopedTestSchema]:
    """scoped_test_schema() on pg_db's entry with test_only=False connections, so the guard runs."""
    resolver = guarded_resolver(pg_db.resolved)
    return scoped_test_schema(resolver.resolve_database(pg_db.resolved.name), prefix=prefix, resolver=resolver)


def _populate_unguarded(pg_db, scoped: ScopedTestSchema) -> None:
    """Create every table in *scoped*'s schemas through pg_db's test-only entry, which skips the guard."""
    create_missing_tables(
        scoped.engine, vocab_engine=scoped.engine, vocabulary_included=True,
        resolved=resolve_with_role_schemas(pg_db.resolved, scoped.schemas),
    )


def test_create_missing_tables_guard_fires_on_reconfigured_schema(pg_db):
    with _guarded_schema(pg_db, "guard_wiring_a") as scoped_a:
        create_missing_tables(scoped_a.engine, vocab_engine=scoped_a.engine, resolved=scoped_a.resolved)

    with _guarded_schema(pg_db, "guard_wiring_b") as scoped_b:
        with pytest.raises(SchemaDriftError):
            create_missing_tables(scoped_b.engine, vocab_engine=scoped_b.engine, resolved=scoped_b.resolved)

        # The guard raises before create_all() runs: schema_b must still be empty.
        with scoped_b.engine.connect() as conn:
            assert sa.inspect(conn).get_table_names(schema=scoped_b.schemas[Role.PRIMARY]) == []


def test_install_fulltext_columns_guard_fires_on_reconfigured_schema(pg_db):
    with _guarded_schema(pg_db, "guard_wiring_ft_a") as scoped_a:
        # Guarded create establishes the provenance baseline for schema_a; an
        # unguarded create here would leave install_fulltext_columns's own guard
        # seeing "tables exist but no record", a false first-time-drift positive.
        create_missing_tables(
            scoped_a.engine, vocab_engine=scoped_a.engine, vocabulary_included=True, resolved=scoped_a.resolved
        )
        install_fulltext_columns(scoped_a.engine, resolved=scoped_a.resolved)

    with _guarded_schema(pg_db, "guard_wiring_ft_b") as scoped_b:
        # install_fulltext_columns wraps every underlying error, including the
        # guard's own SchemaDriftError, in its own FullTextError.
        with pytest.raises(FullTextError) as exc_info:
            install_fulltext_columns(scoped_b.engine, resolved=scoped_b.resolved)
        assert isinstance(exc_info.value.__cause__, SchemaDriftError)

        # The guard raises before any ALTER TABLE runs: schema_b's concept table
        # must not have picked up the tsvector sidecar column.
        schema_b = scoped_b.schemas[Role.VOCAB]
        with scoped_b.engine.connect() as conn:
            if sa.inspect(conn).has_table("concept", schema=schema_b):
                columns = {c["name"] for c in sa.inspect(conn).get_columns("concept", schema=schema_b)}
                assert CONCEPT_NAME_TSVECTOR_COLUMN not in columns


def test_manage_indexes_enable_guard_fires_on_reconfigured_schema(pg_db):
    with _guarded_schema(pg_db, "guard_wiring_idx_a") as scoped_a:
        create_missing_tables(
            scoped_a.engine, vocab_engine=scoped_a.engine, vocabulary_included=True, resolved=scoped_a.resolved
        )
        manage_indexes(scoped_a.engine, vocab_engine=scoped_a.engine, enable=True, cluster=False, resolved=scoped_a.resolved)

    with _guarded_schema(pg_db, "guard_wiring_idx_b") as scoped_b:
        with pytest.raises(SchemaDriftError):
            manage_indexes(
                scoped_b.engine, vocab_engine=scoped_b.engine,
                enable=True,
                cluster=False,
                resolved=scoped_b.resolved,
            )


def test_manage_indexes_disable_guard_fires_on_reconfigured_schema(pg_db):
    """Regression for the disable path specifically: manage_indexes(enable=False)
    used to build no guard at all, regardless of resolved."""
    with _guarded_schema(pg_db, "guard_wiring_idxd_a") as scoped_a:
        create_missing_tables(
            scoped_a.engine, vocab_engine=scoped_a.engine, vocabulary_included=True, resolved=scoped_a.resolved
        )
        manage_indexes(scoped_a.engine, vocab_engine=scoped_a.engine, enable=False, resolved=scoped_a.resolved)

    with _guarded_schema(pg_db, "guard_wiring_idxd_b") as scoped_b:
        _populate_unguarded(pg_db, scoped_b)
        with pytest.raises(SchemaDriftError):
            manage_indexes(
                scoped_b.engine, vocab_engine=scoped_b.engine,
                enable=False,
                resolved=scoped_b.resolved,
            )


def test_truncate_tables_guard_fires_on_reconfigured_schema(pg_db):
    with _guarded_schema(pg_db, "guard_wiring_trunc_a") as scoped_a:
        create_missing_tables(
            scoped_a.engine, vocab_engine=scoped_a.engine, vocabulary_included=True, resolved=scoped_a.resolved
        )
        truncate_tables(
            scoped_a.engine, vocab_engine=scoped_a.engine, scope=TableCategory.VOCABULARY, cascade=True,
            resolved=scoped_a.resolved,
        )

    with _guarded_schema(pg_db, "guard_wiring_trunc_b") as scoped_b:
        _populate_unguarded(pg_db, scoped_b)
        with pytest.raises(SchemaDriftError):
            truncate_tables(
                scoped_b.engine, vocab_engine=scoped_b.engine,
                scope=TableCategory.VOCABULARY,
                cascade=True,
                resolved=scoped_b.resolved,
            )
