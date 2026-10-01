import pytest
from typer.testing import CliRunner

from omop_alchemy.maintenance.cli_tables import analyze_tables
from omop_alchemy.maintenance.cli_schema import create_missing_tables
from omop_alchemy.maintenance.tables import TableCategory

runner = CliRunner()


def test_analyze_tables_runs_on_sqlite(fresh_engine, fresh_resolved):
    """Analyze applies successfully on SQLite for selected OMOP tables."""
    create_missing_tables(fresh_engine, vocab_engine=fresh_engine, vocabulary_included=True, resolved=fresh_resolved)

    results = analyze_tables(
        fresh_engine,
        scope=TableCategory.CLINICAL,
        dry_run=False,
    )

    assert any(
        result.table_name == "person" and result.status == "applied"
        for result in results
    )


def test_analyze_tables_rejects_vacuum_on_sqlite(fresh_engine, fresh_resolved):
    """VACUUM ANALYZE is rejected on SQLite with a clear runtime error."""
    create_missing_tables(fresh_engine, vocab_engine=fresh_engine, vocabulary_included=True, resolved=fresh_resolved)

    with pytest.raises(RuntimeError) as exc_info:
        analyze_tables(fresh_engine, scope=TableCategory.CLINICAL, vacuum=True)

    assert "not supported by the SQLite backend" in str(exc_info.value)
