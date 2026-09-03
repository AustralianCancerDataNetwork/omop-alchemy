"""Schema-provenance guard wired into create_missing_tables().

Live-Postgres regression: proves the guard actually fires and prevents DDL
for a genuinely reconfigured schema, rather than passing by coincidence.

pg_engine (unlike pg_db) is a real, committing engine, so every provenance
row this file's tests write is a genuine commit. No cleanup here yet,
pending a generic test-cleanup interface in oa-configurator's testing
module.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from oa_configurator import ResolvedCDMDatabase, ResolvedConnection, SchemaDriftError
from oa_configurator.testing import isolated_test_schema

from omop_alchemy.maintenance.cli_schema_tables import create_missing_tables

pytestmark = [pytest.mark.postgresql, pytest.mark.db_dialect]


def _resolved(pg_engine, *, database_name: str, schema: str) -> ResolvedCDMDatabase:
    url = pg_engine.url
    connection = ResolvedConnection(
        name="guard_test_conn",
        url=url.render_as_string(hide_password=False),
        safe_url=url.render_as_string(hide_password=True),
        _engine_url=url,
    )
    return ResolvedCDMDatabase(
        name=database_name,
        connection=connection,
        schema_name=schema,
        vocab_connection=connection,
        vocab_schema=schema,
        results_schema=schema,
    )


def test_create_missing_tables_guard_fires_on_reconfigured_schema(pg_engine):
    database_name = f"guard_wiring_test_db_{uuid.uuid4().hex[:8]}"
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
            resolved=_resolved(pg_engine, database_name=database_name, schema=schema_a),
            test_only=False,
        )

        engine_b = pg_engine.execution_options(
            schema_translate_map={None: schema_b, "vocab": schema_b, "results": schema_b}
        )
        with pytest.raises(SchemaDriftError):
            create_missing_tables(
                engine_b,
                db_schema=schema_b,
                resolved=_resolved(pg_engine, database_name=database_name, schema=schema_b),
                test_only=False,
            )

        # The guard raises before create_all() runs: schema_b must still be empty.
        with engine_b.connect() as conn:
            assert sa.inspect(conn).get_table_names(schema=schema_b) == []


def test_create_missing_tables_guard_succeeds_on_agreeing_schema(pg_engine):
    database_name = f"guard_wiring_agree_db_{uuid.uuid4().hex[:8]}"
    with isolated_test_schema(pg_engine, prefix="guard_wiring_agree") as schema:
        engine = pg_engine.execution_options(
            schema_translate_map={None: schema, "vocab": schema, "results": schema}
        )
        resolved = _resolved(pg_engine, database_name=database_name, schema=schema)
        create_missing_tables(engine, db_schema=schema, resolved=resolved, test_only=False)
        # Second call, same resolved schema, nothing missing: must not raise.
        results = create_missing_tables(engine, db_schema=schema, resolved=resolved, test_only=False)
        assert results == []


def test_create_missing_tables_test_only_bypasses_guard(pg_engine):
    database_name = f"guard_wiring_testonly_db_{uuid.uuid4().hex[:8]}"
    with (
        isolated_test_schema(pg_engine, prefix="guard_wiring_to_a") as schema_a,
        isolated_test_schema(pg_engine, prefix="guard_wiring_to_b") as schema_b,
    ):
        engine_a = pg_engine.execution_options(
            schema_translate_map={None: schema_a, "vocab": schema_a, "results": schema_a}
        )
        create_missing_tables(
            engine_a,
            db_schema=schema_a,
            resolved=_resolved(pg_engine, database_name=database_name, schema=schema_a),
            test_only=True,
        )
        engine_b = pg_engine.execution_options(
            schema_translate_map={None: schema_b, "vocab": schema_b, "results": schema_b}
        )
        # Must not raise: test_only=True short-circuits the guard entirely.
        create_missing_tables(
            engine_b,
            db_schema=schema_b,
            resolved=_resolved(pg_engine, database_name=database_name, schema=schema_b),
            test_only=True,
        )
        with engine_b.connect() as conn:
            assert "person" in sa.inspect(conn).get_table_names(schema=schema_b)
