"""Vocab/results-role wiring, exercised on OMOP_Alchemy's own models (Phase 3.2).

Phase 3's vocab/results-role fix tags every vocabulary table with
``schema=Role.VOCAB.value`` and every derived/results table with
``schema=Role.RESULTS.value``. This is its acceptance test: a single
Postgres connection configured with three genuinely different schema
names for ``schema_name``/``vocab_schema``/``results_schema``, confirming
``create_all`` (now role-aware) places each table in the schema its role
says it belongs in, and that a join across a clinical table's concept_id
FK into the vocab schema compiles and executes correctly in one query --
the same-connection case, where this is a single eager join, unlike the
split-connection case covered separately in omop-graph's
``test_vocab_split_connection.py``.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Iterator

import pytest
import sqlalchemy as sa
import sqlalchemy.orm as so

from oa_configurator import Role
from oa_configurator.testing import (
    ScopedTestSchema,
    guarded_resolver,
    reset_schema_registry_rows,
    resolve_with_role_schemas,
    scoped_test_schema,
)

from omop_alchemy.cdm.model.clinical import Observation, Person
from omop_alchemy.cdm.model.derived import Cohort
from omop_alchemy.cdm.model.vocabulary import Concept, Concept_Class, Domain, Vocabulary
from omop_alchemy.maintenance.cli_schema_tables import _create_missing_tables

pytestmark = [pytest.mark.postgresql, pytest.mark.db_dialect]

_TODAY = date(2020, 1, 1)
META_CONCEPT_ID = 0


@pytest.fixture()
def three_schema(pg_db) -> Iterator[ScopedTestSchema]:
    with scoped_test_schema(
        pg_db.resolved, prefix="phase32", split_roles=[Role.VOCAB, Role.RESULTS]
    ) as scoped:
        yield scoped


def _bootstrap_vocab(engine: sa.Engine, vocab_schema: str) -> None:
    """Insert the minimal, real-FK-checked concept-0 bootstrap that every
    concept_id-defaulting column (Person.gender_concept_id and friends,
    Observation's required concept FKs) depends on. Domain/Vocabulary/
    Concept_Class/Concept form a genuine insert cycle in Postgres, and
    disabling triggers for the load and re-enabling them afterwards is the
    same technique production bulk-loads use for this exact reason."""
    vocab_tables = ("domain", "vocabulary", "concept_class", "concept")
    with engine.begin() as conn:
        for table in vocab_tables:
            conn.execute(sa.text(f'ALTER TABLE "{vocab_schema}"."{table}" DISABLE TRIGGER ALL'))

    with so.Session(engine) as session:
        session.add_all(
            [
                Concept(
                    concept_id=META_CONCEPT_ID,
                    concept_name="Meta concept",
                    domain_id="Metadata",
                    vocabulary_id="OMOP",
                    concept_class_id="Metadata",
                    standard_concept="S",
                    concept_code="META",
                    valid_start_date=_TODAY,
                    valid_end_date=date(2099, 12, 31),
                ),
                Domain(domain_id="Metadata", domain_name="Metadata", domain_concept_id=META_CONCEPT_ID),
                Vocabulary(
                    vocabulary_id="OMOP",
                    vocabulary_name="OMOP",
                    vocabulary_reference="local",
                    vocabulary_version="test",
                    vocabulary_concept_id=META_CONCEPT_ID,
                ),
                Concept_Class(
                    concept_class_id="Metadata",
                    concept_class_name="Metadata",
                    concept_class_concept_id=META_CONCEPT_ID,
                ),
            ]
        )
        session.commit()

    with engine.begin() as conn:
        for table in vocab_tables:
            conn.execute(sa.text(f'ALTER TABLE "{vocab_schema}"."{table}" ENABLE TRIGGER ALL'))


def test_tables_land_in_the_schema_their_role_declares(three_schema: ScopedTestSchema) -> None:
    _create_missing_tables(
        three_schema.engine, vocab_engine=three_schema.engine, vocabulary_included=True,
        resolved=three_schema.resolved,
    )

    inspector = sa.inspect(three_schema.engine)
    assert inspector.has_table("person", schema=three_schema.schemas[Role.PRIMARY])
    assert inspector.has_table("observation", schema=three_schema.schemas[Role.PRIMARY])
    assert inspector.has_table("concept", schema=three_schema.schemas[Role.VOCAB])
    assert inspector.has_table("domain", schema=three_schema.schemas[Role.VOCAB])
    assert inspector.has_table("cohort", schema=three_schema.schemas[Role.RESULTS])
    assert inspector.has_table("observation_period", schema=three_schema.schemas[Role.PRIMARY])

    # And not duplicated into the wrong schema.
    assert not inspector.has_table("concept", schema=three_schema.schemas[Role.PRIMARY])
    assert not inspector.has_table("cohort", schema=three_schema.schemas[Role.PRIMARY])


def test_clinical_to_vocab_join_compiles_and_executes_in_one_query(
    three_schema: ScopedTestSchema,
) -> None:
    _create_missing_tables(
        three_schema.engine, vocab_engine=three_schema.engine, vocabulary_included=True,
        resolved=three_schema.resolved,
    )
    _bootstrap_vocab(three_schema.engine, three_schema.schemas[Role.VOCAB])

    with so.Session(three_schema.engine) as session:
        session.add(
            Person(
                person_id=1,
                year_of_birth=1990,
                gender_concept_id=META_CONCEPT_ID,
                race_concept_id=META_CONCEPT_ID,
                ethnicity_concept_id=META_CONCEPT_ID,
            )
        )
        session.commit()

        session.add(
            Observation(
                observation_id=1,
                person_id=1,
                observation_concept_id=META_CONCEPT_ID,
                observation_type_concept_id=META_CONCEPT_ID,
                observation_date=_TODAY,
            )
        )
        session.add(
            Cohort(
                cohort_definition_id=1,
                subject_id=1,
                cohort_start_date=_TODAY,
                cohort_end_date=_TODAY,
            )
        )
        session.commit()

        row = session.execute(
            sa.select(Observation.observation_id, Concept.concept_name).join(
                Concept, Observation.observation_concept_id == Concept.concept_id
            )
        ).one()
        assert row.observation_id == 1
        assert row.concept_name == "Meta concept"

        cohort_row = session.execute(
            sa.select(Cohort.cohort_definition_id).where(Cohort.subject_id == 1)
        ).one()
        assert cohort_row.cohort_definition_id == 1


def test_create_missing_tables_creates_vocab_and_results_schemas_on_a_fresh_database(
    pg_db, pg_engine: sa.Engine, cleanup_after_test
) -> None:
    """_create_missing_tables() used to call ensure_schema() only for the
    primary schema; a fresh database needed vocab/results schemas created too.
    """
    run_id = uuid.uuid4().hex[:8]
    clinical_schema = f"phase32_fresh_clinical_{run_id}"
    vocab_schema = f"phase32_fresh_vocab_{run_id}"
    results_schema = f"phase32_fresh_results_{run_id}"

    def _drop_schemas() -> None:
        with pg_engine.begin() as conn:
            for schema in (clinical_schema, vocab_schema, results_schema):
                conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))

    cleanup_after_test(_drop_schemas)
    reset_schema_registry_rows(cleanup_after_test, pg_engine)
    resolved = resolve_with_role_schemas(
        pg_db.resolved,
        {Role.PRIMARY: clinical_schema, Role.VOCAB: vocab_schema, Role.RESULTS: results_schema},
        resolver=guarded_resolver(pg_db.resolved),
    )
    engine = resolved.create_engine()

    _create_missing_tables(
        engine, vocab_engine=engine,
        vocabulary_included=True,
        resolved=resolved,
    )

    inspector = sa.inspect(engine)
    assert inspector.has_table("person", schema=clinical_schema)
    assert inspector.has_table("concept", schema=vocab_schema)
    assert inspector.has_table("cohort", schema=results_schema)
