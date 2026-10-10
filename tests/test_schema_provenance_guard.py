"""Exercise omop-alchemy's engine and maintenance CLI schema-drift paths."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from oa_configurator import Role, SchemaDriftError
from oa_configurator.testing import (
    guarded_resolver,
    reset_schema_registry_rows,
    resolve_with_role_schemas,
)
from typer.testing import CliRunner

from omop_alchemy.config import OmopAlchemyConfig, create_cdm_engines
from omop_alchemy.maintenance import _cli_utils
from omop_alchemy.maintenance.cli import app

pytestmark = [pytest.mark.postgresql, pytest.mark.db_dialect]

runner = CliRunner()


@pytest.fixture(autouse=True)
def _fresh_role_rows(pg_db, cleanup_after_test):
    reset_schema_registry_rows(cleanup_after_test, pg_db.committing_engine)


@pytest.mark.parametrize("schema", ["pr57_drift_primary_a", "pr57_drift_primary_b"])
def test_create_cdm_engines_rejects_primary_schema_drift(pg_db, schema):
    engines = create_cdm_engines(pg_db.resolved)
    for engine in set(engines):
        engine.dispose()

    resolver = guarded_resolver(pg_db.resolved)
    guarded = resolver.resolve_database(pg_db.resolved.name)
    drifted = resolve_with_role_schemas(
        guarded, {Role.PRIMARY: schema}, resolver=resolver
    )

    with pytest.raises(SchemaDriftError):
        create_cdm_engines(drifted)


def test_create_missing_tables_cli_rejects_drift_before_creating_tables(pg_db, monkeypatch):
    engines = create_cdm_engines(pg_db.resolved)
    for engine in set(engines):
        engine.dispose()

    schema = f"pr57_cli_drift_{uuid.uuid4().hex[:10]}"
    with pg_db.committing_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')

    try:
        resolver = guarded_resolver(pg_db.resolved)
        guarded = resolver.resolve_database(pg_db.resolved.name)
        drifted = resolve_with_role_schemas(
            guarded, {Role.PRIMARY: schema}, resolver=resolver
        )
        monkeypatch.setattr(
            "omop_alchemy.config.get_cdm_context",
            lambda database=None: (OmopAlchemyConfig(), drifted),
        )
        handled_errors = []
        original_handle_error = _cli_utils.handle_error

        def capture_handle_error(error):
            handled_errors.append(error)
            original_handle_error(error)

        monkeypatch.setattr(_cli_utils, "handle_error", capture_handle_error)

        result = runner.invoke(app, ["create-missing-tables"])

        assert result.exit_code == 1
        assert len(handled_errors) == 1
        assert isinstance(handled_errors[0], SchemaDriftError)
        assert "Schema drift detected" in result.output
        assert sa.inspect(pg_db.committing_engine).get_table_names(schema=schema) == []
    finally:
        with pg_db.committing_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
