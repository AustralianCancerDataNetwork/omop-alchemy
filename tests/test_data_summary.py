import sqlalchemy as sa

from omop_alchemy.maintenance.cli_schema import create_missing_tables
from omop_alchemy.maintenance.cli_schema import collect_data_summary


def test_collect_data_summary_can_include_missing_tables(fresh_engine):
    """Test collect data summary can include missing tables."""
    results = collect_data_summary(fresh_engine, existing_only=False)
    assert results
    assert any(result.exists is False for result in results)


def test_collect_data_summary_reports_row_counts(fresh_engine):
    """Test collect data summary reports row counts."""
    create_missing_tables(fresh_engine)

    with fresh_engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO location (location_id) VALUES (1)")
        )

    results = {
        result.table_name: result
        for result in collect_data_summary(fresh_engine, vocabulary_included=True)
    }

    assert results["location"].exists is True
    assert results["location"].row_count == 1


def test_collect_data_summary_excludes_vocabulary_by_default(fresh_engine):
    """Test collect data summary excludes vocabulary by default."""
    create_missing_tables(fresh_engine)

    table_names = {
        result.table_name
        for result in collect_data_summary(fresh_engine)
    }
    assert "person" in table_names
    assert "concept" not in table_names
