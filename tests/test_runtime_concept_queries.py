"""Database-side runtime concept hierarchy predicates."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite

from omop_alchemy.cdm.model.vocabulary import Concept, Concept_Ancestor
from omop_alchemy.toolkit.core.concepts import (
    RuntimeConceptSetSpec,
    descendant_concept_select,
    runtime_concept_predicate,
)


def _add_runtime_hierarchy(session) -> None:
    concepts = (
        (990_000, "runtime root", "S"),
        (990_001, "included standard", "S"),
        (990_002, "included classification", "C"),
        (990_003, "excluded standard", "S"),
        (990_010, "runtime exclusion root", "S"),
    )
    session.add_all(
        Concept(
            concept_id=concept_id,
            concept_name=name,
            domain_id="Condition",
            vocabulary_id="SNOMED",
            concept_class_id="Clinical Finding",
            standard_concept=standardness,
            concept_code=str(concept_id),
            valid_start_date=date(1970, 1, 1),
            valid_end_date=date(2099, 12, 31),
        )
        for concept_id, name, standardness in concepts
    )
    session.flush()
    session.add_all(
        (
            Concept_Ancestor(
                ancestor_concept_id=990_000,
                descendant_concept_id=990_001,
                min_levels_of_separation=1,
                max_levels_of_separation=1,
            ),
            Concept_Ancestor(
                ancestor_concept_id=990_000,
                descendant_concept_id=990_002,
                min_levels_of_separation=1,
                max_levels_of_separation=1,
            ),
            Concept_Ancestor(
                ancestor_concept_id=990_000,
                descendant_concept_id=990_003,
                min_levels_of_separation=1,
                max_levels_of_separation=1,
            ),
            Concept_Ancestor(
                ancestor_concept_id=990_010,
                descendant_concept_id=990_003,
                min_levels_of_separation=1,
                max_levels_of_separation=1,
            ),
        )
    )
    session.flush()


def test_runtime_hierarchy_applies_union_then_exclusion_in_database(session):
    _add_runtime_hierarchy(session)
    spec = RuntimeConceptSetSpec(
        include_ancestor_ids=(990_000,),
        include_exact_ids=(8507,),
        exclude_ancestor_ids=(990_010,),
        exclude_exact_ids=(990_001,),
    )

    statement = sa.select(Concept.concept_id).where(
        runtime_concept_predicate(Concept.concept_id, spec)
    )
    assert session.scalars(statement.order_by(Concept.concept_id)).all() == [
        8507,
        990_002,
    ]


def test_runtime_hierarchy_reuses_standardness_policy(session):
    _add_runtime_hierarchy(session)
    standard_only = RuntimeConceptSetSpec(
        include_ancestor_ids=(990_000,),
        require_standard=True,
        include_classification=False,
    )
    with_classification = RuntimeConceptSetSpec(
        include_ancestor_ids=(990_000,),
        require_standard=True,
        include_classification=True,
    )

    standard_only_statement = sa.select(Concept.concept_id).where(
        runtime_concept_predicate(Concept.concept_id, standard_only)
    )
    with_classification_statement = sa.select(Concept.concept_id).where(
        runtime_concept_predicate(Concept.concept_id, with_classification)
    )

    assert session.scalars(standard_only_statement).all() == [990_001, 990_003]
    assert set(session.scalars(with_classification_statement)) == {
        990_001,
        990_002,
        990_003,
    }


def test_runtime_hierarchy_with_no_inclusions_is_always_false(session):
    statement = sa.select(Concept.concept_id).where(
        runtime_concept_predicate(Concept.concept_id, RuntimeConceptSetSpec())
    )

    assert session.scalars(statement).all() == []


def test_runtime_hierarchy_compiles_to_concept_ancestor_subqueries():
    statement = sa.select(Concept.concept_id).where(
        runtime_concept_predicate(
            Concept.concept_id,
            RuntimeConceptSetSpec(
                include_ancestor_ids=(100,),
                exclude_ancestor_ids=(400,),
                require_standard=True,
            ),
        )
    )

    for dialect in (sqlite.dialect(), postgresql.dialect()):
        compiled = str(statement.compile(dialect=dialect))
        assert "concept_ancestor" in compiled
        assert "standard_concept" in compiled


def test_descendant_select_returns_overlapping_descendant_once(session):
    _add_runtime_hierarchy(session)

    descendants = session.scalars(descendant_concept_select((990_000, 990_010))).all()

    assert descendants.count(990_003) == 1


def test_exact_runtime_id_does_not_depend_on_a_concept_row(session):
    configured_id = -1
    statement = sa.select(sa.literal(configured_id)).where(
        runtime_concept_predicate(
            sa.literal(configured_id),
            RuntimeConceptSetSpec(include_exact_ids=(configured_id,)),
        )
    )

    assert session.scalar(statement) == configured_id


def test_exclusion_wins_over_an_exact_inclusion(session):
    _add_runtime_hierarchy(session)
    spec = RuntimeConceptSetSpec(
        include_exact_ids=(990_003,),
        exclude_ancestor_ids=(990_010,),
    )
    statement = sa.select(Concept.concept_id).where(
        runtime_concept_predicate(Concept.concept_id, spec)
    )

    assert session.scalars(statement).all() == []


def test_standardness_applies_to_exact_inclusions(session):
    _add_runtime_hierarchy(session)
    standard_only = RuntimeConceptSetSpec(
        include_exact_ids=(990_002,),
        require_standard=True,
        include_classification=False,
    )
    with_classification = RuntimeConceptSetSpec(
        include_exact_ids=(990_002,),
        require_standard=True,
        include_classification=True,
    )

    standard_only_statement = sa.select(Concept.concept_id).where(
        runtime_concept_predicate(Concept.concept_id, standard_only)
    )
    with_classification_statement = sa.select(Concept.concept_id).where(
        runtime_concept_predicate(Concept.concept_id, with_classification)
    )

    assert session.scalars(standard_only_statement).all() == []
    assert session.scalars(with_classification_statement).all() == [990_002]
