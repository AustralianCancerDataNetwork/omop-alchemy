import sqlalchemy as sa

from omop_alchemy.maintenance.cli_schema import _create_missing_tables
from omop_alchemy.maintenance.cli_schema import collect_data_summary
from omop_alchemy.maintenance.context import MaintenanceContext


def test_collect_data_summary_can_include_missing_tables(fresh_engine, fresh_resolved):
    """Test collect data summary can include missing tables."""
    results = collect_data_summary(MaintenanceContext(resolved=fresh_resolved, engine=fresh_engine, vocab_engine=fresh_engine), existing_only=False)
    assert results
    assert any(result.exists is False for result in results)


def test_collect_data_summary_reports_row_counts(fresh_engine, fresh_resolved):
    """Test collect data summary reports row counts."""
    _create_missing_tables(MaintenanceContext(resolved=fresh_resolved, engine=fresh_engine, vocab_engine=fresh_engine))

    with fresh_engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO location (location_id) VALUES (1)")
        )

    results = {
        result.table_name: result
        for result in collect_data_summary(MaintenanceContext(resolved=fresh_resolved, engine=fresh_engine, vocab_engine=fresh_engine), vocabulary_included=True)
    }

    assert results["location"].exists is True
    assert results["location"].row_count == 1


def test_collect_data_summary_excludes_vocabulary_by_default(fresh_engine, fresh_resolved):
    """Test collect data summary excludes vocabulary by default."""
    _create_missing_tables(MaintenanceContext(resolved=fresh_resolved, engine=fresh_engine, vocab_engine=fresh_engine))

    table_names = {
        result.table_name
        for result in collect_data_summary(MaintenanceContext(resolved=fresh_resolved, engine=fresh_engine, vocab_engine=fresh_engine))
    }
    assert "person" in table_names
    assert "concept" not in table_names
