"""Stored tsvector columns are referenced through ``fulltext_vector_column()``
and never attached to the shared ORM tables.

The column is bound to its table for qualification and schema translation,
but the ORM does not know it: it works in WHERE and ORDER BY, and is never
part of an entity load.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import cast

import pytest
import sqlalchemy as sa
import sqlalchemy.orm as so
from sqlalchemy.dialects import postgresql
from oa_configurator import Role
from oa_configurator.testing import ScopedTestSchema, scoped_test_schema
from orm_loader.helpers import bulk_load_context

from omop_alchemy.backends import (
    CONCEPT_NAME_TSVECTOR_COLUMN,
    CONCEPT_SYNONYM_NAME_TSVECTOR_COLUMN,
    FullTextError,
    PostgresBackend,
)
from omop_alchemy.cdm.model.vocabulary import Concept, Concept_Synonym, Domain
from omop_alchemy.maintenance.cli_fulltext import install_fulltext_columns, populate_fulltext_columns
from omop_alchemy.maintenance.cli_schema_tables import create_missing_tables
from omop_alchemy.maintenance.cli_vocab import load_vocab_source
from tests.conftest import _ATHENA_FIXTURE_DATA, _write_fixture_csv

pytestmark = [pytest.mark.postgresql, pytest.mark.db_dialect]

_backend = PostgresBackend()
_CONCEPT = cast(sa.Table, Concept.__table__)
_CONCEPT_ID = 4181412


@pytest.fixture
def installed(pg_db) -> Iterator[ScopedTestSchema]:
    """A scoped schema with fulltext installed and one populated concept."""
    with scoped_test_schema(pg_db.resolved, prefix="fulltext_column") as scoped:
        create_missing_tables(scoped.engine, vocab_engine=scoped.engine, resolved=scoped.resolved)
        install_fulltext_columns(scoped.engine, resolved=scoped.resolved)
        with so.Session(scoped.engine) as session:
            with bulk_load_context(session):
                session.add(
                    Concept(
                        concept_id=_CONCEPT_ID,
                        concept_name="Malignant neoplasm of ovary",
                        domain_id="Condition",
                        vocabulary_id="SNOMED",
                        concept_class_id="Clinical Finding",
                        standard_concept="S",
                        concept_code="363443007",
                        valid_start_date=date(2000, 1, 1),
                        valid_end_date=date(2099, 12, 31),
                    )
                )
                session.flush()
            session.commit()
        populate_fulltext_columns(scoped.engine)
        yield scoped


def _query() -> sa.ColumnElement:
    return sa.func.plainto_tsquery("english", "ovary")


def test_column_is_bound_to_the_table_without_attaching_it(installed: ScopedTestSchema) -> None:
    column = _backend.fulltext_vector_column(installed.engine, _CONCEPT)

    assert column.table is _CONCEPT
    assert CONCEPT_NAME_TSVECTOR_COLUMN not in _CONCEPT.c
    stmt = (
        sa.select(Concept.concept_id)
        .join(Domain, Domain.domain_id == Concept.domain_id)
        .where(column.op("@@")(_query()))
    )
    rendered = str(stmt.compile(dialect=postgresql.dialect(), schema_translate_map={"vocab": "vocab"}))
    assert f"__[SCHEMA_vocab].concept.{CONCEPT_NAME_TSVECTOR_COLUMN}" in rendered
    assert rendered.count("FROM ") == 1


def test_synonym_table_resolves_its_own_column(installed: ScopedTestSchema) -> None:
    column = _backend.fulltext_vector_column(installed.engine, cast(sa.Table, Concept_Synonym.__table__))

    assert column.name == CONCEPT_SYNONYM_NAME_TSVECTOR_COLUMN
    assert CONCEPT_SYNONYM_NAME_TSVECTOR_COLUMN not in Concept_Synonym.__table__.c


def test_column_works_in_where_and_order_by(installed: ScopedTestSchema) -> None:
    column = _backend.fulltext_vector_column(installed.engine, _CONCEPT)
    stmt = (
        sa.select(Concept.concept_id)
        .where(column.op("@@")(_query()))
        .order_by(sa.func.ts_rank(column, _query()).desc())
    )

    with installed.engine.connect() as connection:
        assert connection.execute(stmt).scalars().all() == [_CONCEPT_ID]


def test_orm_does_not_know_the_column(installed: ScopedTestSchema) -> None:
    _backend.fulltext_vector_column(installed.engine, _CONCEPT)

    assert not hasattr(Concept, CONCEPT_NAME_TSVECTOR_COLUMN)
    assert CONCEPT_NAME_TSVECTOR_COLUMN not in str(sa.select(Concept))
    with so.Session(installed.engine) as session:
        loaded = session.get(Concept, _CONCEPT_ID)
    assert loaded is not None
    assert CONCEPT_NAME_TSVECTOR_COLUMN not in vars(loaded)


def test_missing_column_raises(pg_db) -> None:
    with scoped_test_schema(pg_db.resolved, prefix="fulltext_missing") as scoped:
        create_missing_tables(scoped.engine, vocab_engine=scoped.engine, resolved=scoped.resolved)
        with pytest.raises(FullTextError, match=CONCEPT_NAME_TSVECTOR_COLUMN):
            _backend.fulltext_vector_column(scoped.engine, _CONCEPT)


def test_vocab_load_after_install_succeeds_in_a_schema_without_the_column(
    installed: ScopedTestSchema,
    pg_db,
    tmp_path: Path,
) -> None:
    columns_before = set(_CONCEPT.c.keys())
    source_path = tmp_path / "athena_source"
    source_path.mkdir()
    for table_name, data in _ATHENA_FIXTURE_DATA.items():
        _write_fixture_csv(source_path, table_name, data)

    with scoped_test_schema(pg_db.resolved, prefix="fulltext_regression_load") as other:
        report = load_vocab_source(
            other.engine, vocab_engine=other.engine,
            source_path=source_path,
            db_schema=other.schemas[Role.PRIMARY],
            resolved=other.resolved,
        )

    assert all(result.status == "loaded" for result in report.results if result.required)
    assert set(_CONCEPT.c.keys()) == columns_before
