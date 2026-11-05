"""DuckDB-specific CDM schema creation: the real end-to-end exercise of
oa-configurator's DuckDB DDL shims against the full ~179-FK CDM graph, not
just the 2-table reproduction used to design them."""

from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from omop_alchemy.cdm.model.clinical import Condition_Occurrence, Person
from omop_alchemy.cdm.model.structural.episode import Episode
from omop_alchemy.cdm.model.vocabulary import (
    Concept,
    Concept_Class,
    Domain,
    Relationship,
    Vocabulary,
)
from omop_alchemy.maintenance.cli_schema import _create_missing_tables
from omop_alchemy.maintenance.cli_vocab import _load_vocab_model_csv
from omop_alchemy.maintenance.context import MaintenanceContext
from tests.conftest import _ATHENA_FIXTURE_DATA, _write_fixture_csv


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_duckdb_create_missing_tables_can_create_vocabulary(fresh_duckdb_engine, fresh_duckdb_resolved):
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
        # Create a real concept row so the enforced FKs resolve
        session.add(Domain(domain_id="x", domain_name="x", domain_concept_id=0))
        session.add(Vocabulary(vocabulary_id="x", vocabulary_name="x", vocabulary_concept_id=0))
        session.add(Concept_Class(concept_class_id="x", concept_class_name="x", concept_class_concept_id=0))
        session.commit()

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
    """Cyclical FK: domain.domain_concept_id -> concept. An orphaned
    reference inserts without error through FK deferral."""
    _create_missing_tables(
        MaintenanceContext(
            resolved=fresh_duckdb_resolved, engine=fresh_duckdb_engine, vocab_engine=fresh_duckdb_engine
        ),
        vocabulary_included=True,
    )

    with Session(fresh_duckdb_engine) as session:
        session.add(Domain(domain_id="Drug", domain_name="Drug", domain_concept_id=999))
        session.commit()


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_duckdb_load_file_self_referential_relationship_pair(fresh_duckdb_engine, fresh_duckdb_resolved, tmp_path):
    """Self-referential relationships: relationship.reverse_relationship_id."""
    _create_missing_tables(
        MaintenanceContext(
            resolved=fresh_duckdb_resolved, engine=fresh_duckdb_engine, vocab_engine=fresh_duckdb_engine
        ),
        vocabulary_included=True,
    )

    with Session(fresh_duckdb_engine) as session:
        # relationship_concept_id -> concept is a real, inline-enforced FK
        # so a real concept row needs to exist first.
        session.add(Domain(domain_id="Episode", domain_name="Episode", domain_concept_id=0))
        session.add(Vocabulary(vocabulary_id="Episode", vocabulary_name="Episode", vocabulary_concept_id=0))
        session.add(
            Concept_Class(concept_class_id="Episode", concept_class_name="Episode", concept_class_concept_id=0)
        )
        session.commit()
        session.add(
            Concept(
                concept_id=0, concept_name="test", domain_id="Episode", vocabulary_id="Episode",
                concept_class_id="Episode", concept_code="x",
                valid_start_date=date(2000, 1, 1), valid_end_date=date(2000, 1, 1),
            )
        )
        session.commit()

    csv_path = _write_fixture_csv(tmp_path, "relationship", _ATHENA_FIXTURE_DATA["relationship"])
    with Session(fresh_duckdb_engine) as session:
        rows = _load_vocab_model_csv(
            session, model=Relationship, csv_path=csv_path, merge_strategy="upsert", quote_mode="by_delimiter",  # ty: ignore[invalid-argument-type]
            resolved=fresh_duckdb_resolved,
        )
        session.commit()
    assert rows == 2

    with Session(fresh_duckdb_engine) as session:
        rel = session.get(Relationship, "Is a")
        assert rel is not None
        assert rel.reverse_relationship_id == "Subsumes"


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_duckdb_episode_self_reference_is_not_db_enforced(fresh_duckdb_engine, fresh_duckdb_resolved):
    """Self-referential relationships: episode.episode_parent_id."""
    _create_missing_tables(
        MaintenanceContext(
            resolved=fresh_duckdb_resolved, engine=fresh_duckdb_engine, vocab_engine=fresh_duckdb_engine
        ),
        vocabulary_included=True,
    )

    with Session(fresh_duckdb_engine) as session:
        session.add(Domain(domain_id="Episode", domain_name="Episode", domain_concept_id=0))
        session.add(Vocabulary(vocabulary_id="Episode", vocabulary_name="Episode", vocabulary_concept_id=0))
        session.add(
            Concept_Class(concept_class_id="Episode", concept_class_name="Episode", concept_class_concept_id=0)
        )
        session.commit()
        session.add(
            Concept(
                concept_id=0, concept_name="test", domain_id="Episode", vocabulary_id="Episode",
                concept_class_id="Episode", concept_code="x",
                valid_start_date=date(2000, 1, 1), valid_end_date=date(2000, 1, 1),
            )
        )
        session.commit()

        session.add(
            Person(person_id=1, gender_concept_id=0, year_of_birth=2000, race_concept_id=0, ethnicity_concept_id=0)
        )
        session.commit()

        session.add(
            Episode(
                episode_id=1,
                person_id=1,
                episode_start_date=date(2020, 1, 1),
                episode_concept_id=0,
                episode_object_concept_id=0,
                episode_type_concept_id=0,
                episode_parent_id=999,
            )
        )
        session.commit()
