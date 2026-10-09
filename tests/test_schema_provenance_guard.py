"""Schema-provenance enforcement reaching _create_missing_tables(),
_install_fulltext_columns(), _manage_indexes(), and _truncate_tables().

Live-Postgres regression: proves a reconfigured schema is caught before any
of these functions can run DDL, rather than passing by coincidence.

Enforcement now happens at create_engine() construction time: 
the SchemaDriftError below is raised while building scoped_b's own engine,
before any of these functions are even reached. One case per call site is 
still kept here, each proving the specific downstream function genuinely 
never runs. A regression in any one of them reaching the database before 
construction could otherwise go unnoticed.
"""

from __future__ import annotations

from contextlib import AbstractContextManager

import pytest
from oa_configurator import SchemaDriftError
from oa_configurator.testing import (
    ScopedTestSchema,
    guarded_resolver,
    reset_schema_registry_rows,
    scoped_test_schema,
)

from omop_alchemy.maintenance.cli_fulltext import _install_fulltext_columns
from omop_alchemy.maintenance.cli_indexes import _manage_indexes
from omop_alchemy.maintenance.cli_schema_tables import _create_missing_tables
from omop_alchemy.maintenance.cli_tables import _truncate_tables
from omop_alchemy.maintenance.tables import TableCategory
from omop_alchemy.maintenance.context import MaintenanceContext

pytestmark = [pytest.mark.postgresql, pytest.mark.db_dialect]


@pytest.fixture(autouse=True)
def _fresh_role_rows(pg_db, cleanup_after_test):
    reset_schema_registry_rows(cleanup_after_test, pg_db.committing_engine)


def _guarded_schema(pg_db, prefix: str) -> AbstractContextManager[ScopedTestSchema]:
    """scoped_test_schema() on pg_db's entry with test_only=False connections, so the guard runs."""
    resolver = guarded_resolver(pg_db.resolved)
    return scoped_test_schema(resolver.resolve_database(pg_db.resolved.name), prefix=prefix, resolver=resolver)


def test_create_missing_tables_guard_fires_on_reconfigured_schema(pg_db):
    with _guarded_schema(pg_db, "guard_wiring_a") as scoped_a:
        _create_missing_tables(MaintenanceContext(resolved=scoped_a.resolved, engine=scoped_a.engine, vocab_engine=scoped_a.engine))

    # Drift is now caught at create_engine() construction time before
    # _create_missing_tables() is even reachable.
    with pytest.raises(SchemaDriftError):
        with _guarded_schema(pg_db, "guard_wiring_b"):
            pass


def test_install_fulltext_columns_guard_fires_on_reconfigured_schema(pg_db):
    with _guarded_schema(pg_db, "guard_wiring_ft_a") as scoped_a:
        # Guarded create establishes the provenance baseline for schema_a; an
        # unguarded create here would leave _install_fulltext_columns's own guard
        # seeing "tables exist but no record", a false first-time-drift positive.
        _create_missing_tables(
            MaintenanceContext(resolved=scoped_a.resolved, engine=scoped_a.engine, vocab_engine=scoped_a.engine),
            vocabulary_included=True,
        )
        _install_fulltext_columns(MaintenanceContext(resolved=scoped_a.resolved, engine=scoped_a.engine, vocab_engine=scoped_a.engine))

    # Drift is now caught at create_engine() construction time, before
    # _install_fulltext_columns() is even reachable.
    with pytest.raises(SchemaDriftError):
        with _guarded_schema(pg_db, "guard_wiring_ft_b"):
            pass


def test_manage_indexes_enable_guard_fires_on_reconfigured_schema(pg_db):
    with _guarded_schema(pg_db, "guard_wiring_idx_a") as scoped_a:
        _create_missing_tables(
            MaintenanceContext(resolved=scoped_a.resolved, engine=scoped_a.engine, vocab_engine=scoped_a.engine),
            vocabulary_included=True,
        )
        _manage_indexes(MaintenanceContext(resolved=scoped_a.resolved, engine=scoped_a.engine, vocab_engine=scoped_a.engine), enable=True, cluster=False)

    # Drift is now caught at create_engine() construction time, before
    # _manage_indexes() is even reachable.
    with pytest.raises(SchemaDriftError):
        with _guarded_schema(pg_db, "guard_wiring_idx_b"):
            pass


def test_manage_indexes_disable_guard_fires_on_reconfigured_schema(pg_db):
    """Regression for the disable path specifically: _manage_indexes(enable=False)
    used to build no guard at all, regardless of resolved."""
    with _guarded_schema(pg_db, "guard_wiring_idxd_a") as scoped_a:
        _create_missing_tables(
            MaintenanceContext(resolved=scoped_a.resolved, engine=scoped_a.engine, vocab_engine=scoped_a.engine),
            vocabulary_included=True,
        )
        _manage_indexes(MaintenanceContext(resolved=scoped_a.resolved, engine=scoped_a.engine, vocab_engine=scoped_a.engine), enable=False)

    # Drift is now caught at create_engine() construction time, before
    # _manage_indexes() is even reachable.
    with pytest.raises(SchemaDriftError):
        with _guarded_schema(pg_db, "guard_wiring_idxd_b"):
            pass


def test_truncate_tables_guard_fires_on_reconfigured_schema(pg_db):
    with _guarded_schema(pg_db, "guard_wiring_trunc_a") as scoped_a:
        _create_missing_tables(
            MaintenanceContext(resolved=scoped_a.resolved, engine=scoped_a.engine, vocab_engine=scoped_a.engine),
            vocabulary_included=True,
        )
        _truncate_tables(
            MaintenanceContext(resolved=scoped_a.resolved, engine=scoped_a.engine, vocab_engine=scoped_a.engine),
            scope=TableCategory.VOCABULARY,
            cascade=True,
        )

    # Drift is now caught at create_engine() construction time, before
    # _truncate_tables() is even reachable.
    with pytest.raises(SchemaDriftError):
        with _guarded_schema(pg_db, "guard_wiring_trunc_b"):
            pass
