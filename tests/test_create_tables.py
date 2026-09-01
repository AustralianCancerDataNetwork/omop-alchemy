import sqlalchemy as sa

from omop_alchemy.maintenance.cli_schema import collect_missing_tables, create_missing_tables


def test_collect_missing_tables_on_empty_database(fresh_engine):
    """An empty database reports core clinical and vocabulary tables as missing."""
    missing = collect_missing_tables(fresh_engine)

    table_names = {table.table_name for table in missing}
    assert "person" in table_names
    assert "concept" in table_names


def test_create_missing_tables_reports_blocked_tables_when_vocabulary_is_missing(fresh_engine):
    """Non-vocabulary creation reports blocked tables when required vocab tables are excluded."""
    results = create_missing_tables(fresh_engine, vocabulary_included=False)

    inspector = sa.inspect(fresh_engine)
    assert results
    assert not inspector.has_table("concept")
    result_by_name = {
        result.table_name: result
        for result in results
    }
    assert result_by_name["person"].status == "blocked"
    assert "concept" in result_by_name["person"].detail


def test_create_missing_tables_can_recreate_non_vocabulary_tables_when_dependencies_exist(fresh_engine):
    """Previously dropped non-vocabulary tables can be recreated when dependencies are present."""
    create_missing_tables(fresh_engine, vocabulary_included=True)

    with fresh_engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE cdm_source")

    results = create_missing_tables(fresh_engine, vocabulary_included=False)

    inspector = sa.inspect(fresh_engine)
    assert any(result.table_name == "cdm_source" and result.status == "created" for result in results)
    assert inspector.has_table("cdm_source")
    assert inspector.has_table("concept")


def test_create_missing_tables_can_create_vocabulary(fresh_engine):
    """Including vocabulary creates both clinical and vocabulary tables."""
    create_missing_tables(fresh_engine, vocabulary_included=True)

    inspector = sa.inspect(fresh_engine)
    assert inspector.has_table("person")
    assert inspector.has_table("concept")
