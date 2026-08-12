"""Tests for ConceptValidationMixin._non_standard_concepts_for_column, the
referenced-concept standard-concept check.

Regression coverage for the §3.1 behaviour change: ConceptValidationMixin now
delegates to Concept.is_standard_expr() instead of its own hand-rolled check,
closing two bugs (blank/whitespace flags and non-canonical values such as 'X'
previously passed as valid) without reopening the NULL three-valued-logic trap
that motivated is_not(True) over sa.not_(...).

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
        (2, "C", False),
        (3, None, True),
        (4, "", True),
        (5, "   ", True),
        (6, "X", True),
    ],
)
def test_non_standard_concepts_for_column_matches_is_standard_expr(
    session, case_id, standard_concept, expect_flagged
):
    """Pins the §3.1 behaviour change: blank, whitespace, and 'X' are now
    correctly flagged as non-standard (previously bugs let them through),
    while 'S'/'C' remain accepted. A NULL standard_concept must still be
    flagged — the sa.not_(...) form this replaced would have silently missed
    it, since NOT (x IN (...)) is NULL, not TRUE, when x is NULL."""
    concept_id = 900000 + case_id
    condition_occurrence_id = 800000 + case_id
    _seed_dirty_concept(session, concept_id, standard_concept)
    _seed_condition(
        session,
        condition_occurrence_id=condition_occurrence_id,
        condition_concept_id=concept_id,
        type_concept_id=_type_concept_id(session),
    )

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
