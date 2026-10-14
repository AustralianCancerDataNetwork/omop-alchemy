"""DuckDB-specific CDM schema creation: the real end-to-end exercise of
oa-configurator's DuckDB DDL shims against the full ~179-FK CDM graph, not
just the 2-table reproduction used to design them."""

from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from omop_alchemy.cdm.model.clinical import Condition_Occurrence, Person
from omop_alchemy.cdm.model.vocabulary import Concept
from omop_alchemy.maintenance.cli_schema import _create_missing_tables
from omop_alchemy.maintenance.context import MaintenanceContext


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_duckdb_create_missing_tables_can_create_vocabulary(fresh_duckdb_engine, fresh_duckdb_resolved):
    """The full CDM (clinical + vocabulary) creates successfully on DuckDB,
    including the tables caught in genuine FK cycles (person/visit_occurrence/
    visit_detail, concept/concept_class/domain/vocabulary)."""
    _create_missing_tables(
        MaintenanceContext(
            resolved=fresh_duckdb_resolved, engine=fresh_duckdb_engine, vocab_engine=fresh_duckdb_engine
        ),
        vocabulary_included=True,
    )

    inspector = sa.inspect(fresh_duckdb_engine)
    assert inspector.has_table("person")
    assert inspector.has_table("concept")
    assert inspector.has_table("visit_occurrence")
    assert inspector.has_table("visit_detail")


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_duckdb_noncircular_fk_still_enforced(fresh_duckdb_engine, fresh_duckdb_resolved):
    """condition_occurrence.person_id -> person is not part of any FK cycle,
    so it renders inline and stays DB-enforced on DuckDB same as any other
    dialect: inserting an orphaned reference raises."""
    _create_missing_tables(
        MaintenanceContext(
            resolved=fresh_duckdb_resolved, engine=fresh_duckdb_engine, vocab_engine=fresh_duckdb_engine
        ),
        vocabulary_included=True,
    )

    with Session(fresh_duckdb_engine) as session:
        # concept_id=0's own FKs (domain_id/vocabulary_id/concept_class_id) are
        # part of the vocabulary cycle and unenforced, so arbitrary values are fine.
        session.add(
            Concept(
                concept_id=0,
                concept_name="test concept",
                domain_id="x",
                vocabulary_id="x",
                concept_class_id="x",
                concept_code="x",
                valid_start_date=date(2000, 1, 1),
                valid_end_date=date(2000, 1, 1),
            )
        )
        session.commit()

    with Session(fresh_duckdb_engine) as session, pytest.raises(IntegrityError):
        session.add(
            Condition_Occurrence(
                condition_occurrence_id=1,
                person_id=999,
                condition_concept_id=0,
                condition_start_date=date(2020, 1, 1),
                condition_type_concept_id=0,
            )
        )
        session.commit()


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_duckdb_circular_fk_is_not_db_enforced(fresh_duckdb_engine, fresh_duckdb_resolved):
    """person.visit_occurrence_id -> visit_occurrence is part of the genuine
    person/visit_occurrence/visit_detail FK cycle, deferred by SQLAlchemy and
    no-oped by oa-configurator's _skip_deferred_fk_on_duckdb shim (DuckDB
    can't execute the ALTER TABLE ADD CONSTRAINT): an orphaned reference
    inserts without error, the documented tradeoff for a cyclic CDM schema
    on DuckDB."""
    _create_missing_tables(
        MaintenanceContext(
            resolved=fresh_duckdb_resolved, engine=fresh_duckdb_engine, vocab_engine=fresh_duckdb_engine
        ),
        vocabulary_included=True,
    )

    with Session(fresh_duckdb_engine) as session:
        session.add(
            Person(
                person_id=1,
                gender_concept_id=0,
                year_of_birth=2000,
                race_concept_id=0,
                ethnicity_concept_id=0,
                visit_occurrence_id=999,
            )
        )
        session.commit()
