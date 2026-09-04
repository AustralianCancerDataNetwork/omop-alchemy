"""Schema-provenance guard wired into create_missing_tables().

Live-Postgres regression: proves the guard actually fires and prevents DDL
for a genuinely reconfigured schema, rather than passing by coincidence.

Only that one case is covered here. The guard's own agree/no-op/test_only
semantics are already exhaustively covered at the primitive level in
oa-configurator's own test suite; what's worth proving per consuming repo
is that this call site is actually wired to it, and a wiring mistake would
show up here too.

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
from oa_configurator.domains.resources.sql import SCHEMA_PROVENANCE_SCHEMA, _schema_provenance_table

from oa_configurator.testing import delete_rows_on_cleanup, isolated_test_schema

from omop_alchemy.maintenance.cli_schema_tables import create_missing_tables

pytestmark = [pytest.mark.postgresql, pytest.mark.db_dialect]


def _resolved(pg_db, *, database_name: str, schema: str):
    """pg_db.resolved with a unique name (the guard's own key includes it),
    all three schemas pointed at schema, and connection.test_only forced
    False so the guard doesn't no-op against pg_db's own test-only marking.
    """
    return dataclasses.replace(
        pg_db.resolved,
        name=database_name,
        schema_name=schema,
        vocab_schema=schema,
        results_schema=schema,
        connection=dataclasses.replace(pg_db.resolved.connection, test_only=False),
    )


def test_create_missing_tables_guard_fires_on_reconfigured_schema(pg_db, pg_engine, cleanup_after_test):
    database_name = f"guard_wiring_test_db_{uuid.uuid4().hex[:8]}"
    table = _schema_provenance_table(SCHEMA_PROVENANCE_SCHEMA)
    delete_rows_on_cleanup(
        cleanup_after_test, pg_engine, table, table.c.database_name == database_name
    )
    with (
        isolated_test_schema(pg_engine, prefix="guard_wiring_a") as schema_a,
        isolated_test_schema(pg_engine, prefix="guard_wiring_b") as schema_b,
    ):
        engine_a = pg_engine.execution_options(
            schema_translate_map={None: schema_a, "vocab": schema_a, "results": schema_a}
        )
        create_missing_tables(
            engine_a,
            db_schema=schema_a,
            resolved=_resolved(pg_db, database_name=database_name, schema=schema_a),
        )

        engine_b = pg_engine.execution_options(
            schema_translate_map={None: schema_b, "vocab": schema_b, "results": schema_b}
        )
        with pytest.raises(SchemaDriftError):
            create_missing_tables(
                engine_b,
                db_schema=schema_b,
                resolved=_resolved(pg_db, database_name=database_name, schema=schema_b),
            )

        # The guard raises before create_all() runs: schema_b must still be empty.
        with engine_b.connect() as conn:
            assert sa.inspect(conn).get_table_names(schema=schema_b) == []
