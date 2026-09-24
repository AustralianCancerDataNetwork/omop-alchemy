import dataclasses

import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role, register_reserved_schema_tag
from orm_loader.helpers import Base

from omop_alchemy.maintenance import cli_schema_tables
from omop_alchemy.maintenance.cli_schema import collect_missing_tables, create_missing_tables
from omop_alchemy.maintenance.tables import MaintenanceTable, TableCategory

from tests.conftest import resolved_cdm_database_from_engine


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


def test_create_missing_tables_creates_table_under_a_non_role_schema_tag(fresh_engine, monkeypatch):
    """A table tagged with a registered schema tag outside Role is created,
    not silently skipped by a Role-only enumeration.

    Uses a fake MaintenanceTable: tagging a real CDM table this way needs
    extension-table support, not yet built on this branch.
    """
    register_reserved_schema_tag("synthetic_tag", owner="test")
    engine = fresh_engine.execution_options(
        schema_translate_map={Role.PRIMARY.value: None, "vocab": None, "results": None, "synthetic_tag": None}
    )
    resolved = resolved_cdm_database_from_engine(engine, name="synthetic_tag_test")
    test_connection = dataclasses.replace(resolved.connection, test_only=True)
    resolved = dataclasses.replace(resolved, connection=test_connection, vocab_connection=test_connection)

    class _SyntheticTagTable(Base):
        __tablename__ = "synthetic_tag_table"
        __table_args__ = {"schema": "synthetic_tag"}
        id: so.Mapped[int] = so.mapped_column(primary_key=True)

    fake_table = MaintenanceTable(
        table_name="synthetic_tag_table",
        model_name="_SyntheticTagTable",
        model_module=__name__,
        category=TableCategory.METADATA,
        table=_SyntheticTagTable.__table__,  # ty: ignore[invalid-argument-type]
        primary_key_columns=tuple(_SyntheticTagTable.__table__.primary_key.columns),  # ty: ignore[unresolved-attribute]
    )
    monkeypatch.setattr(cli_schema_tables, "collect_missing_tables", lambda *a, **k: [fake_table])

    try:
        results = create_missing_tables(engine, resolved=resolved)
        result_by_name = {result.table_name: result for result in results}
        assert result_by_name["synthetic_tag_table"].status == "created"
        assert sa.inspect(engine).has_table("synthetic_tag_table")
    finally:
        Base.registry._dispose_cls(_SyntheticTagTable)
        Base.metadata.remove(_SyntheticTagTable.__table__)  # ty: ignore[invalid-argument-type]
