import pytest
import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role, SchemaClaim
from orm_loader.helpers import Base

from omop_alchemy.maintenance import cli_schema_tables
from omop_alchemy.maintenance.cli_schema import (
    _create_missing_tables,
    collect_missing_tables,
)
from omop_alchemy.maintenance.context import MaintenanceContext
from omop_alchemy.maintenance.tables import MaintenanceTable, TableCategory


def test_collect_missing_tables_on_empty_database(fresh_engine, fresh_resolved):
    """An empty database reports core clinical and vocabulary tables as missing."""
    missing = collect_missing_tables(MaintenanceContext(resolved=fresh_resolved, engine=fresh_engine, vocab_engine=fresh_engine))

    table_names = {table.table_name for table in missing}
    assert "person" in table_names
    assert "concept" in table_names


def test_create_missing_tables_reports_blocked_tables_when_vocabulary_is_missing(fresh_engine, fresh_resolved):
    """Non-vocabulary creation reports blocked tables when required vocab tables are excluded."""
    results = _create_missing_tables(MaintenanceContext(resolved=fresh_resolved, engine=fresh_engine, vocab_engine=fresh_engine), vocabulary_included=False)

    inspector = sa.inspect(fresh_engine)
    assert results
    assert not inspector.has_table("concept")
    result_by_name = {
        result.table_name: result
        for result in results
    }
    assert result_by_name["person"].status == "blocked"
    assert "concept" in result_by_name["person"].detail


def test_create_missing_tables_can_recreate_non_vocabulary_tables_when_dependencies_exist(fresh_engine, fresh_resolved):
    """Previously dropped non-vocabulary tables can be recreated when dependencies are present."""
    _create_missing_tables(MaintenanceContext(resolved=fresh_resolved, engine=fresh_engine, vocab_engine=fresh_engine), vocabulary_included=True)

    with fresh_engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE cdm_source")

    results = _create_missing_tables(MaintenanceContext(resolved=fresh_resolved, engine=fresh_engine, vocab_engine=fresh_engine), vocabulary_included=False)

    inspector = sa.inspect(fresh_engine)
    assert any(result.table_name == "cdm_source" and result.status == "created" for result in results)
    assert inspector.has_table("cdm_source")
    assert inspector.has_table("concept")


def test_create_missing_tables_can_create_vocabulary(fresh_engine, fresh_resolved):
    """Including vocabulary creates both clinical and vocabulary tables."""
    _create_missing_tables(MaintenanceContext(resolved=fresh_resolved, engine=fresh_engine, vocab_engine=fresh_engine), vocabulary_included=True)

    inspector = sa.inspect(fresh_engine)
    assert inspector.has_table("person")
    assert inspector.has_table("concept")


def test_create_missing_tables_creates_table_under_a_non_role_schema_tag(fresh_resolved, monkeypatch):
    """A table tagged with a registered schema tag outside Role is created,
    not silently skipped by a Role-only enumeration.

    Uses a fake MaintenanceTable: tagging a real CDM table this way needs
    extension-table support, not yet built on this branch.
    """
    engine, _ = fresh_resolved.create_engines(
        schema_claims=[SchemaClaim(schema_tag="synthetic_tag", physical_schema=None)]
    )

    class _SyntheticTagTable(Base):
        __tablename__ = "synthetic_tag_table"
        __table_args__ = {"schema": "synthetic_tag"}
        id: so.Mapped[int] = so.mapped_column(primary_key=True)

    fake_table = MaintenanceTable(
        table_name="synthetic_tag_table",
        model_name="_SyntheticTagTable",
        model_module=__name__,
        model_cls=_SyntheticTagTable,
        category=TableCategory.METADATA,
        table=_SyntheticTagTable.__table__,  # ty: ignore[invalid-argument-type]
        primary_key_columns=tuple(_SyntheticTagTable.__table__.primary_key.columns),  # ty: ignore[unresolved-attribute]
    )
    monkeypatch.setattr(
        cli_schema_tables,
        "missing_maintenance_targets",
        lambda context, **_: context.targets([fake_table]),
    )

    try:
        results = _create_missing_tables(MaintenanceContext(resolved=fresh_resolved, engine=engine, vocab_engine=engine))
        result_by_name = {result.table_name: result for result in results}
        assert result_by_name["synthetic_tag_table"].status == "created"
        assert sa.inspect(engine).has_table("synthetic_tag_table")
    finally:
        engine.dispose()
        Base.registry._dispose_cls(_SyntheticTagTable)
        Base.metadata.remove(_SyntheticTagTable.__table__)  # ty: ignore[invalid-argument-type]


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_targets_resolves_fk_from_a_schema_claims_only_tag_on_duckdb(fresh_duckdb_resolved):
    """A foreign key from a tag claimed only via create_engine(schema_claims=[...]),
    not present in resolved's own configured schema-translate map, must resolve
    through foreign_key_can_span() instead of raising UnregisteredSchemaTagError."""
    resolved = fresh_duckdb_resolved
    primary_schema = resolved.configured_internal_schema_translate_map()[Role.PRIMARY]
    engine, vocab_engine = resolved.create_engines(
        schema_claims=[SchemaClaim(schema_tag="synthetic_tag", physical_schema=primary_schema)]
    )

    class _SyntheticParentTable(Base):
        __tablename__ = "synthetic_parent_table"
        __table_args__ = {"schema": Role.PRIMARY.value}
        id: so.Mapped[int] = so.mapped_column(primary_key=True)

    class _SyntheticChildTable(Base):
        __tablename__ = "synthetic_child_table"
        __table_args__ = {"schema": "synthetic_tag"}
        id: so.Mapped[int] = so.mapped_column(primary_key=True)
        parent_id: so.Mapped[int] = so.mapped_column(
            sa.ForeignKey(f"{Role.PRIMARY.value}.synthetic_parent_table.id")
        )

    fake_parent = MaintenanceTable(
        table_name=_SyntheticParentTable.__tablename__,
        model_name=_SyntheticParentTable.__name__,
        model_module=__name__,
        model_cls=_SyntheticParentTable,
        category=TableCategory.METADATA,
        table=_SyntheticParentTable.__table__,  # ty: ignore[invalid-argument-type]
        primary_key_columns=tuple(_SyntheticParentTable.__table__.primary_key.columns),  # ty: ignore[unresolved-attribute]
    )
    fake_child = MaintenanceTable(
        table_name=_SyntheticChildTable.__tablename__,
        model_name=_SyntheticChildTable.__name__,
        model_module=__name__,
        model_cls=_SyntheticChildTable,
        category=TableCategory.METADATA,
        table=_SyntheticChildTable.__table__,  # ty: ignore[invalid-argument-type]
        primary_key_columns=tuple(_SyntheticChildTable.__table__.primary_key.columns),  # ty: ignore[unresolved-attribute]
    )

    try:
        context = MaintenanceContext(resolved=resolved, engine=engine, vocab_engine=vocab_engine)
        targets = context.targets([fake_parent, fake_child])
        child_target = next(target for target in targets if target.table.table_name == "synthetic_child_table")
        assert child_target.foreign_keys_creatable is True
    finally:
        engine.dispose()
        if vocab_engine is not engine:
            vocab_engine.dispose()
        Base.registry._dispose_cls(_SyntheticChildTable)
        Base.registry._dispose_cls(_SyntheticParentTable)
        Base.metadata.remove(_SyntheticChildTable.__table__)  # ty: ignore[invalid-argument-type]
        Base.metadata.remove(_SyntheticParentTable.__table__)  # ty: ignore[invalid-argument-type]
