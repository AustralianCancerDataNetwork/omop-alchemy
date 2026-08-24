"""Tests for ConceptValidationMixin._non_standard_concepts_for_column, the
referenced-concept standard-concept check.

Regression coverage for the standard-concept predicate change: ConceptValidationMixin now
delegates to Concept.is_standard_expr() instead of its own hand-rolled check,
so classification concepts are rejected as mapping targets alongside blank,
whitespace, and non-canonical values such as 'X', without reopening the NULL
three-valued-logic trap that motivated is_not(True) over sa.not_(...).

_non_standard_concepts_for_column takes its table/column as plain parameters
and does not use ``cls``, so it is exercised directly on
``ConceptValidationMixin`` against ``Condition_Occurrence``'s real mapped
table, without needing the mixin to actually be applied to a mapped class.
"""

from datetime import date

import pytest
import sqlalchemy as sa

from omop_alchemy.cdm.base import ConceptValidationMixin
from omop_alchemy.cdm.model.clinical import Condition_Occurrence
from omop_alchemy.cdm.model.vocabulary import Concept


def _type_concept_id(session) -> int:
    return session.scalar(
        sa.select(Concept.concept_id).where(Concept.domain_id == "Type Concept").limit(1)
    )


def _seed_dirty_concept(session, concept_id, standard_concept):
    session.add(
        Concept(
            concept_id=concept_id,
            concept_name=f"fixture concept {concept_id}",
            domain_id="Condition",
            vocabulary_id="SNOMED",
            concept_class_id="Clinical Finding",
            standard_concept=standard_concept,
            concept_code=f"fixture-{concept_id}",
            valid_start_date=date(1970, 1, 1),
            valid_end_date=date(2099, 12, 31),
        )
    )
    session.flush()


def _seed_condition(session, *, condition_occurrence_id, condition_concept_id, type_concept_id):
    session.add(
        Condition_Occurrence(
            condition_occurrence_id=condition_occurrence_id,
            person_id=1,
            condition_concept_id=condition_concept_id,
            condition_start_date=date(2020, 1, 1),
            condition_type_concept_id=type_concept_id,
            condition_source_value="concept-validation-fixture",
        )
    )
    session.flush()


def _violations_for(session, condition_occurrence_id) -> set[int]:
    table = Condition_Occurrence.__table__
    col = table.c.condition_concept_id
    stmt = ConceptValidationMixin._non_standard_concepts_for_column(
        table=table, col=col
    ).where(table.c.condition_occurrence_id == condition_occurrence_id)
    return {int(cid) for (cid,) in session.execute(stmt)}


@pytest.mark.parametrize(
    "case_id, standard_concept, expect_flagged",
    [
        (1, "S", False),
        (2, "C", True),
        (3, None, True),
        (4, "", True),
        (5, "   ", True),
        (6, "X", True),
    ],
)
def test_non_standard_concepts_for_column_matches_is_standard_expr(
    session, case_id, standard_concept, expect_flagged
):
    """Python and SQL predicates agree, and only ``S`` is a mapping target.

    The explicit negation assertion protects the complement semantics for
    NULL and blank values, where a bare SQL ``NOT`` can otherwise produce
    NULL instead of ``TRUE``.
    """
    concept_id = 900000 + case_id
    condition_occurrence_id = 800000 + case_id
    _seed_dirty_concept(session, concept_id, standard_concept)
    _seed_condition(
        session,
        condition_occurrence_id=condition_occurrence_id,
        condition_concept_id=concept_id,
        type_concept_id=_type_concept_id(session),
    )

    concept = session.get(Concept, concept_id)
    assert concept is not None
    expected_standard = standard_concept is not None and standard_concept.strip() == "S"
    expected_classification = (
        standard_concept is not None and standard_concept.strip() == "C"
    )
    assert concept.is_standard is expected_standard
    assert concept.is_classification is expected_classification

    sql_standard, sql_classification, sql_not_standard = session.execute(
        sa.select(
            Concept.is_standard_expr(),
            Concept.is_classification_expr(),
            ~Concept.is_standard_expr(),
        ).where(Concept.concept_id == concept_id)
    ).one()
    assert bool(sql_standard) is expected_standard
    assert bool(sql_classification) is expected_classification
    assert bool(sql_not_standard) is not expected_standard

    violations = _violations_for(session, condition_occurrence_id)
    assert (concept_id in violations) is expect_flagged


def test_non_standard_concepts_for_column_flags_missing_concept_row(session):
    """A dangling *_concept_id with no matching concept row at all is a
    violation, not silently ignored by the outer join."""
    missing_concept_id = 900099
    condition_occurrence_id = 800099
    _seed_condition(
        session,
        condition_occurrence_id=condition_occurrence_id,
        condition_concept_id=missing_concept_id,
        type_concept_id=_type_concept_id(session),
    )

    violations = _violations_for(session, condition_occurrence_id)
    assert missing_concept_id in violations
