"""Rectify CLI: acknowledge-schema-migration / drop-orphan-schema-tables.

Live-Postgres regression against real StackConfig/Resolver plumbing, not
mocked, since these commands' whole point is writing/reading real database
state. Monkeypatches cli_schema.load_stack_config (the name as imported into
that module) to point at a scratch StackConfig built from pg_engine's own
connection, rather than touching the real on-disk config.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from oa_configurator import CDMDatabaseConfig, ConnectionConfig, StackConfig
from oa_configurator.domains.resources.sql import _provenance_schema_for, _schema_provenance_table, ensure_schema
from oa_configurator.testing import isolated_test_schema
from sqlalchemy.engine import make_url
from typer.testing import CliRunner

from omop_alchemy.maintenance.cli import app

pytestmark = [pytest.mark.postgresql, pytest.mark.db_dialect]

runner = CliRunner()


@pytest.fixture
def cli_stack(pg_engine, monkeypatch):
    """A StackConfig with one real, test_only Postgres connection/database
    entry, pointed at pg_engine's own live server, the same connection every
    other Postgres test in this repo already uses."""
    url = make_url(pg_engine.url).render_as_string(hide_password=False)
    conn_url = make_url(url)
    stack = StackConfig.for_session(
        connections={
            "cli_rectify_conn": ConnectionConfig(
                dialect=conn_url.drivername,
                host=conn_url.host,
                port=conn_url.port,
                user=conn_url.username,
                password=conn_url.password,
                database_name=conn_url.database,
                test_only=True,
            )
        },
        databases={
            "cli_rectify_db": CDMDatabaseConfig(connection="cli_rectify_conn", schema_name="public"),
        },
    )
    monkeypatch.setattr("omop_alchemy.maintenance.cli_schema.load_stack_config", lambda: stack)
    return stack


def _cleanup_provenance_row(pg_engine, *, database_name: str, role: str) -> None:
    with pg_engine.begin() as conn:
        ensure_schema(conn, _provenance_schema_for(conn))
        table = _schema_provenance_table(_provenance_schema_for(conn))
        table.create(bind=conn, checkfirst=True)
        conn.execute(
            table.delete().where(table.c.database_name == database_name, table.c.role == role)
        )


def test_reason_is_mandatory(cli_stack):
    result = runner.invoke(
        app,
        ["acknowledge-schema-migration", "--database", "cli_rectify_db", "--new-schema", "whatever"],
    )
    assert result.exit_code != 0
    assert "--reason" in result.output


def test_acknowledge_writes_a_provenance_row(cli_stack, pg_engine):
    role = "vocab"
    schema = f"cli_ack_{uuid.uuid4().hex[:8]}"
    try:
        result = runner.invoke(
            app,
            [
                "acknowledge-schema-migration",
                "--database", "cli_rectify_db",
                "--role", role,
                "--new-schema", schema,
                "--reason", "regression test",
            ],
        )
        assert result.exit_code == 0, result.stdout
        assert "Acknowledged" in result.stdout

        with pg_engine.connect() as conn:
            table = _schema_provenance_table(_provenance_schema_for(conn))
            row = conn.execute(
                sa.select(table.c.resolved_schema, table.c.reason).where(
                    table.c.database_name == "cli_rectify_db", table.c.role == role
                )
            ).first()
        assert row is not None
        assert row.resolved_schema == schema
        assert row.reason == "regression test"
    finally:
        _cleanup_provenance_row(pg_engine, database_name="cli_rectify_db", role=role)


def test_drop_orphan_refuses_against_a_databases_own_current_schema(cli_stack):
    result = runner.invoke(
        app,
        ["drop-orphan-schema-tables", "--database", "cli_rectify_db", "--schema", "public"],
    )
    assert result.exit_code != 0
    assert "current schema target" in result.stdout


def test_drop_orphan_previews_without_confirm(cli_stack, pg_engine):
    with isolated_test_schema(pg_engine, prefix="cli_drop_preview") as schema:
        with pg_engine.begin() as conn:
            conn.exec_driver_sql(f'CREATE TABLE "{schema}".orphaned (id int)')

        result = runner.invoke(
            app, ["drop-orphan-schema-tables", "--database", "cli_rectify_db", "--schema", schema]
        )
        assert result.exit_code == 0, result.stdout
        assert "Would drop" in result.stdout
        assert "Preview only" in result.stdout

        with pg_engine.connect() as conn:
            assert sa.inspect(conn).has_table("orphaned", schema=schema)


def test_drop_orphan_confirm_actually_drops(cli_stack, pg_engine):
    with isolated_test_schema(pg_engine, prefix="cli_drop_confirm") as schema:
        with pg_engine.begin() as conn:
            conn.exec_driver_sql(f'CREATE TABLE "{schema}".orphaned (id int)')

        result = runner.invoke(
            app,
            [
                "drop-orphan-schema-tables",
                "--database", "cli_rectify_db",
                "--schema", schema,
                "--confirm",
            ],
        )
        assert result.exit_code == 0, result.stdout
        assert "Dropped" in result.stdout

        with pg_engine.connect() as conn:
            assert not sa.inspect(conn).has_table("orphaned", schema=schema)
