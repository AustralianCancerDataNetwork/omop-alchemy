import pytest

from omop_alchemy.maintenance.cli_schema_info import collect_maintenance_info
from omop_alchemy.maintenance.context import MaintenanceContext


def _status_by_command(info):
    return {cs.command_name: cs.status for cs in info.command_support}


def test_command_support_reports_sqlite_unsupported_for_capabilities_it_lacks(fresh_engine, fresh_resolved):
    """SQLite has no owned-sequence reset, batch truncate, or FK anti-join support."""
    info = collect_maintenance_info(MaintenanceContext(resolved=fresh_resolved, engine=fresh_engine, vocab_engine=fresh_engine))
    statuses = _status_by_command(info)

    assert statuses["reset-sequences"] == "unsupported"
    assert statuses["truncate-tables"] == "unsupported"
    assert statuses["foreign-keys validate"] == "unsupported"
    assert statuses["foreign-keys disable"] == "unsupported"


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_command_support_reports_duckdb_ready_for_sequence_truncate_and_fk_validate(
    fresh_duckdb_engine, fresh_duckdb_resolved
):
    """DuckDB implements set_sequence_value, truncate_table_batch, and
    count_fk_violations. DuckDB still has no RI triggers, so the 
    trigger-based commands stay unsupported."""
    info = collect_maintenance_info(
        MaintenanceContext(resolved=fresh_duckdb_resolved, engine=fresh_duckdb_engine, vocab_engine=fresh_duckdb_engine)
    )
    statuses = _status_by_command(info)

    assert statuses["reset-sequences"] == "ready"
    assert statuses["truncate-tables"] == "ready"
    assert statuses["foreign-keys validate"] == "ready"
    assert statuses["foreign-keys disable"] == "unsupported"
    assert statuses["foreign-keys status"] == "unsupported"


@pytest.mark.postgresql
@pytest.mark.db_dialect
def test_command_support_reports_postgres_ready_for_every_capability(pg_db, pg_engine, pg_unscoped_resolved):
    info = collect_maintenance_info(
        MaintenanceContext(resolved=pg_unscoped_resolved, engine=pg_engine, vocab_engine=pg_engine)
    )
    statuses = _status_by_command(info)

    assert statuses["reset-sequences"] == "ready"
    assert statuses["truncate-tables"] == "ready"
    assert statuses["foreign-keys validate"] == "ready"
    assert statuses["foreign-keys disable"] == "ready"
    assert statuses["backup-database"] in {"ready", "blocked"}
