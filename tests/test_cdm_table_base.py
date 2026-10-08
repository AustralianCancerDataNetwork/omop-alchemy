"""Tests for CDMTableBase / CDMExtensionTableBase auto-tagging via __init_subclass__."""

import uuid
from contextlib import contextmanager
from typing import Iterator

import pytest
import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role, SchemaClaim
from oa_configurator.testing import reset_schema_registry_rows
from orm_loader.helpers import Base

from omop_alchemy.cdm.base import CDMExtensionTableBase, CDMTableBase
from omop_alchemy.cdm.base.cdm_table_base import MODEL_MODULE_PREFIX, _redeclares_a_tagged_ancestors_table
from omop_alchemy.config import CDM_EXTENSION_SCHEMA, CDM_EXTENSION_SCHEMA_OWNER
from omop_alchemy.maintenance.cli_schema import _create_missing_tables
from omop_alchemy.maintenance.tables import _mapped_cdm_table_classes, collect_maintenance_tables


@contextmanager
def _cleanup_table(table_name: str, *, schema: str | None) -> Iterator[None]:
    """Hide a table's class from maintenance scans and drop it from Base's metadata.

    By table name, not class reference: a class whose __init_subclass__
    raises after SQLAlchemy has already mapped it never binds a name
    locally. SQLAlchemy can't fully unmap a class mid-process, so this just
    flips __omop_is_cdm_table__ False (what maintenance scans gate on) and
    leaves the class/mapper as an inert orphan.
    """
    try:
        yield
    finally:
        key = f"{schema}.{table_name}" if schema else table_name
        table = Base.metadata.tables.get(key)
        if table is None:
            return
        mapper = next((m for m in Base.registry.mappers if m.local_table is table), None)
        if mapper is not None:
            mapper.class_.__omop_is_cdm_table__ = False
        Base.metadata.remove(table)


# ---------------------------------------------------------------------------
# _redeclares_a_tagged_ancestors_table: pure unit tests, no SQLAlchemy mapping
# ---------------------------------------------------------------------------

def test_redeclares_a_tagged_ancestors_table_detects_same_tablename_redeclare():
    class _TaggedAncestor:
        __omop_is_cdm_table__ = True
        __tablename__ = "concept"

    class _Redeclaring(_TaggedAncestor):
        __tablename__ = "concept"

    class _GenuinelyNew(_TaggedAncestor):
        __tablename__ = "other_table"

    assert _redeclares_a_tagged_ancestors_table(_Redeclaring) is True
    assert _redeclares_a_tagged_ancestors_table(_GenuinelyNew) is False


# ---------------------------------------------------------------------------
# CDMTableBase: internal (non-extension) tagging
# ---------------------------------------------------------------------------

def test_internal_module_table_is_tagged_with_its_category():
    class _FakeInternal(CDMTableBase):
        __module__ = f"{MODEL_MODULE_PREFIX}fake_domain"
        __tablename__ = "zz_test_cdm_table_base_internal"
        __table_args__ = {"schema": Role.PRIMARY.value}
        id: so.Mapped[int] = so.mapped_column(primary_key=True)

    with _cleanup_table("zz_test_cdm_table_base_internal", schema=Role.PRIMARY.value):
        assert _FakeInternal.__omop_is_cdm_table__ is True
        assert _FakeInternal.__omop_table_category__ == "fake_domain"
        assert _FakeInternal.__omop_extension_owner__ is None
        assert sa.inspect(_FakeInternal, raiseerr=False) is not None


def test_internal_module_table_rejects_missing_schema_tag():
    with pytest.raises(TypeError), _cleanup_table(
        "zz_test_cdm_table_base_bad_schema", schema=None
    ):
        class _FakeInternalBadSchema(CDMTableBase):
            __module__ = f"{MODEL_MODULE_PREFIX}fake_domain"
            __tablename__ = "zz_test_cdm_table_base_bad_schema"
            id: so.Mapped[int] = so.mapped_column(primary_key=True)


def test_internal_module_table_rejects_extension_table_base():
    """A class defined under omop_alchemy.cdm.model.* must use CDMTableBase directly."""
    with pytest.raises(TypeError, match="CDMTableBase"), _cleanup_table(
        "zz_test_cdm_table_base_internal_ext", schema=CDM_EXTENSION_SCHEMA
    ):
        class _FakeInternalViaExtension(CDMExtensionTableBase):
            __module__ = f"{MODEL_MODULE_PREFIX}fake_domain"
            __tablename__ = "zz_test_cdm_table_base_internal_ext"
            id: so.Mapped[int] = so.mapped_column(primary_key=True)


def test_external_module_table_rejects_cdm_table_base():
    """A class outside omop_alchemy.cdm.model.* must use CDMExtensionTableBase, not CDMTableBase."""
    with pytest.raises(TypeError, match="CDMExtensionTableBase"), _cleanup_table(
        "zz_test_cdm_table_base_external", schema=Role.PRIMARY.value
    ):
        class _FakeExternalViaInternal(CDMTableBase):
            __module__ = "tests.fake_extension_pkg.tables"
            __tablename__ = "zz_test_cdm_table_base_external"
            __table_args__ = {"schema": Role.PRIMARY.value}
            id: so.Mapped[int] = so.mapped_column(primary_key=True)


# ---------------------------------------------------------------------------
# CDMTableBase: abstract intermediates are never tagged, at any depth
# ---------------------------------------------------------------------------

def test_abstract_intermediates_are_never_tagged_two_levels_deep():
    class _Abstract1(CDMTableBase):
        __abstract__ = True

    class _Abstract2(_Abstract1):
        __abstract__ = True

    assert _Abstract1.__omop_is_cdm_table__ is False
    assert _Abstract2.__omop_is_cdm_table__ is False


# ---------------------------------------------------------------------------
# CDMExtensionTableBase: extension-owner tagging, schema default and lockdown
# ---------------------------------------------------------------------------

def test_extension_table_is_tagged_with_its_owner_and_no_category():
    class _FakeExtension(CDMExtensionTableBase):
        __module__ = "tests.fake_extension_pkg.tables"
        __tablename__ = "zz_test_cdm_table_base_extension"
        id: so.Mapped[int] = so.mapped_column(primary_key=True)

    with _cleanup_table("zz_test_cdm_table_base_extension", schema=CDM_EXTENSION_SCHEMA):
        assert _FakeExtension.__omop_is_cdm_table__ is True
        assert _FakeExtension.__omop_table_category__ is None
        assert _FakeExtension.__omop_extension_owner__ == "tests"
        assert _FakeExtension.__table__.schema == CDM_EXTENSION_SCHEMA
        assert sa.inspect(_FakeExtension, raiseerr=False) is not None


def test_extension_table_cannot_override_its_schema():
    with pytest.raises(TypeError, match=CDM_EXTENSION_SCHEMA), _cleanup_table(
        "zz_test_cdm_table_base_ext_override", schema=Role.PRIMARY.value
    ):
        class _FakeExtensionOverride(CDMExtensionTableBase):
            __module__ = "tests.fake_extension_pkg.tables"
            __tablename__ = "zz_test_cdm_table_base_ext_override"
            __table_args__ = {"schema": Role.PRIMARY.value}
            id: so.Mapped[int] = so.mapped_column(primary_key=True)


def test_extension_table_may_redeclare_table_args_matching_the_default_schema():
    """A subclass needing its own __table_args__ (e.g. for a ForeignKeyConstraint)
    stays valid as long as it restates the same, default extension schema."""
    class _FakeExtensionRestated(CDMExtensionTableBase):
        __module__ = "tests.fake_extension_pkg.tables"
        __tablename__ = "zz_test_cdm_table_base_ext_restated"
        __table_args__ = {"schema": CDM_EXTENSION_SCHEMA}
        id: so.Mapped[int] = so.mapped_column(primary_key=True)

    with _cleanup_table("zz_test_cdm_table_base_ext_restated", schema=CDM_EXTENSION_SCHEMA):
        assert _FakeExtensionRestated.__omop_is_cdm_table__ is True
        assert _FakeExtensionRestated.__table__.schema == CDM_EXTENSION_SCHEMA


# ---------------------------------------------------------------------------
# Real production model tree: regression coverage for the ~38-file migration
# ---------------------------------------------------------------------------

def test_every_mapped_cdm_class_is_inspectable():
    classes = list(_mapped_cdm_table_classes())
    assert len(classes) > 0
    for cls in classes:
        assert sa.inspect(cls, raiseerr=False) is not None


def test_category_and_extension_table_owner_are_mutually_exclusive():
    tables = collect_maintenance_tables()
    assert tables
    for table in tables:
        assert (table.category is None) == table.is_extension


# ---------------------------------------------------------------------------
# End-to-end: a real create_missing_tables() run creates an extension table
# ---------------------------------------------------------------------------

def test_extension_table_created_via_create_missing_tables_on_sqlite(fresh_resolved):
    """SQLite has no schema concept: the extension tag must resolve to
    unqualified, not a literal 'extension.' prefix SQLite can't create.

    fresh_engine isn't used here: it has no claim for CDM_EXTENSION_SCHEMA
    (that claim is create_cdm_engine()'s own responsibility, covered by
    test_config_driver.py), so this builds its own engine with the tag
    explicitly claimed, matching how a real CDM engine resolves it.
    """
    class _FakeExtensionSqlite(CDMExtensionTableBase):
        __module__ = "tests.fake_extension_pkg.sqlite_smoke"
        __tablename__ = "zz_test_cdm_table_base_sqlite_smoke"
        id: so.Mapped[int] = so.mapped_column(primary_key=True)

    engine = fresh_resolved.create_engine(
        schema_claims=[SchemaClaim(schema_tag=CDM_EXTENSION_SCHEMA, physical_schema=CDM_EXTENSION_SCHEMA, reserved=True)]
    )
    with _cleanup_table("zz_test_cdm_table_base_sqlite_smoke", schema=CDM_EXTENSION_SCHEMA):
        results = _create_missing_tables(engine, vocab_engine=engine, resolved=fresh_resolved)
        result_by_name = {result.table_name: result for result in results}

        assert result_by_name["zz_test_cdm_table_base_sqlite_smoke"].status == "created"
        assert result_by_name["zz_test_cdm_table_base_sqlite_smoke"].category is None
        assert sa.inspect(engine).has_table("zz_test_cdm_table_base_sqlite_smoke", schema=None)


@pytest.mark.postgresql
@pytest.mark.db_dialect
def test_extension_table_creates_its_schema_on_postgres(pg_db, pg_engine, cleanup_after_test):
    """Ensure_schema must actually CREATE the extension schema when
    it doesn't exist yet, not just resolve to a name.
    """
    class _FakeExtensionPg(CDMExtensionTableBase):
        __module__ = "tests.fake_extension_pkg.pg_smoke"
        __tablename__ = "zz_test_cdm_table_base_pg_smoke"
        id: so.Mapped[int] = so.mapped_column(primary_key=True)

    extension_schema = f"zz_ext_smoke_{uuid.uuid4().hex[:12]}"
    reset_schema_registry_rows(cleanup_after_test, pg_engine, schema_tags=[CDM_EXTENSION_SCHEMA])
    assert extension_schema not in sa.inspect(pg_engine).get_schema_names()

    with _cleanup_table("zz_test_cdm_table_base_pg_smoke", schema=CDM_EXTENSION_SCHEMA):
        engine = pg_db.resolved.create_engine(
            schema_claims=[SchemaClaim(
                schema_tag=CDM_EXTENSION_SCHEMA,
                physical_schema=extension_schema,
                owner=CDM_EXTENSION_SCHEMA_OWNER,
            )],
        )
        try:
            assert extension_schema in sa.inspect(engine).get_schema_names()

            _create_missing_tables(engine, vocab_engine=engine, resolved=pg_db.resolved)

            inspector = sa.inspect(engine)
            assert extension_schema in inspector.get_schema_names()
            assert inspector.has_table("zz_test_cdm_table_base_pg_smoke", schema=extension_schema)
        finally:
            with pg_engine.begin() as connection:
                connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{extension_schema}" CASCADE'))
