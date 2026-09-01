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

from contextlib import ExitStack
from datetime import date
from typing import Iterator, NamedTuple

import pytest
import sqlalchemy as sa
import sqlalchemy.orm as so

from oa_configurator.testing import isolated_test_schema

from omop_alchemy.cdm.model.clinical import Observation, Person
from omop_alchemy.cdm.model.derived import Cohort
from omop_alchemy.cdm.model.vocabulary import Concept, Concept_Class, Domain, Vocabulary
from omop_alchemy.maintenance.cli_schema_tables import create_missing_tables

pytestmark = [pytest.mark.postgresql, pytest.mark.db_dialect]

_TODAY = date(2020, 1, 1)
META_CONCEPT_ID = 0


class _ThreeSchema(NamedTuple):
    engine: sa.Engine
    clinical_schema: str
    vocab_schema: str
    results_schema: str


@pytest.fixture()
def three_schema(pg_engine: sa.Engine) -> Iterator[_ThreeSchema]:
    with ExitStack() as stack:
        clinical_schema = stack.enter_context(isolated_test_schema(pg_engine, prefix="phase32_clinical"))
        vocab_schema = stack.enter_context(isolated_test_schema(pg_engine, prefix="phase32_vocab"))
        results_schema = stack.enter_context(isolated_test_schema(pg_engine, prefix="phase32_results"))

        engine = pg_engine.execution_options(
            schema_translate_map={
                None: clinical_schema,
                "vocab": vocab_schema,
                "results": results_schema,
            }
        )
        yield _ThreeSchema(
            engine=engine,
            clinical_schema=clinical_schema,
            vocab_schema=vocab_schema,
            results_schema=results_schema,
        )


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


def test_tables_land_in_the_schema_their_role_declares(three_schema: _ThreeSchema) -> None:
    create_missing_tables(
        three_schema.engine, db_schema=three_schema.clinical_schema, vocabulary_included=True
    )

    inspector = sa.inspect(three_schema.engine)
    assert inspector.has_table("person", schema=three_schema.clinical_schema)
    assert inspector.has_table("observation", schema=three_schema.clinical_schema)
    assert inspector.has_table("concept", schema=three_schema.vocab_schema)
    assert inspector.has_table("domain", schema=three_schema.vocab_schema)
    assert inspector.has_table("cohort", schema=three_schema.results_schema)
    assert inspector.has_table("observation_period", schema=three_schema.results_schema)

    # And not duplicated into the wrong schema.
    assert not inspector.has_table("concept", schema=three_schema.clinical_schema)
    assert not inspector.has_table("cohort", schema=three_schema.clinical_schema)


def test_clinical_to_vocab_join_compiles_and_executes_in_one_query(
    three_schema: _ThreeSchema,
) -> None:
    create_missing_tables(
        three_schema.engine, db_schema=three_schema.clinical_schema, vocabulary_included=True
    )
    _bootstrap_vocab(three_schema.engine, three_schema.vocab_schema)

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
