"""Standard concept mapping query contracts."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.dialects import postgresql, sqlite

from omop_alchemy.cdm.model.vocabulary import Concept, Concept_Relationship
from omop_alchemy.toolkit.core.concepts import (
    STANDARD_CONCEPT_MAPPING_UNIQUENESS,
    StandardConceptMappingSpec,
    standard_concept_mapping_select,
)


SOURCE_ID = 991_000
STANDARD_TARGET_ID = 991_001
SECOND_STANDARD_TARGET_ID = 991_002
NON_STANDARD_TARGET_ID = 991_003
INVALID_TARGET_ID = 991_004
STANDARD_SOURCE_ID = 991_005


def _concept(
    concept_id: int,
    name: str,
    *,
    standard_concept: str | None,
    invalid_reason: str | None = None,
) -> Concept:
    return Concept(
        concept_id=concept_id,
        concept_name=name,
        domain_id="Condition",
        vocabulary_id="SNOMED",
        concept_class_id="Clinical Finding",
        standard_concept=standard_concept,
        concept_code=f"code-{concept_id}",
        valid_start_date=date(2000, 1, 1),
        valid_end_date=date(2099, 12, 31),
        invalid_reason=invalid_reason,
    )


def _relationship(
    source_id: int,
    target_id: int,
    relationship_id: str = "Maps to",
    *,
    valid_end_date: date = date(2099, 12, 31),
    invalid_reason: str | None = None,
) -> Concept_Relationship:
    return Concept_Relationship(
        concept_id_1=source_id,
        concept_id_2=target_id,
        relationship_id=relationship_id,
        valid_start_date=date(2000, 1, 1),
        valid_end_date=valid_end_date,
        invalid_reason=invalid_reason,
    )


def _add_vocabulary_rows(session) -> None:
    session.add_all(
        (
            _concept(SOURCE_ID, "source", standard_concept=None),
            _concept(STANDARD_TARGET_ID, "first standard", standard_concept="S"),
            _concept(
                SECOND_STANDARD_TARGET_ID,
                "second standard",
                standard_concept="S",
            ),
            _concept(NON_STANDARD_TARGET_ID, "non-standard", standard_concept=None),
            _concept(
                INVALID_TARGET_ID,
                "invalid standard",
                standard_concept="S",
                invalid_reason="D",
            ),
            _concept(STANDARD_SOURCE_ID, "standard source", standard_concept="S"),
        )
    )
    session.flush()
    session.add_all(
        (
            _relationship(SOURCE_ID, STANDARD_TARGET_ID),
            _relationship(SOURCE_ID, SECOND_STANDARD_TARGET_ID),
            _relationship(SOURCE_ID, NON_STANDARD_TARGET_ID),
            _relationship(SOURCE_ID, INVALID_TARGET_ID),
            _relationship(
                SOURCE_ID,
                STANDARD_SOURCE_ID,
                valid_end_date=date(2020, 12, 31),
                invalid_reason="U",
            ),
            _relationship(SOURCE_ID, STANDARD_TARGET_ID, "Maps to value"),
            _relationship(STANDARD_SOURCE_ID, STANDARD_SOURCE_ID),
        )
    )
    session.flush()


def test_mapping_preserves_multiple_standard_targets(session):
    _add_vocabulary_rows(session)

    rows = (
        session.execute(
            standard_concept_mapping_select(
                StandardConceptMappingSpec(source_concept_ids=(SOURCE_ID,))
            )
        )
        .mappings()
        .all()
    )

    assert {row["standard_concept_id"] for row in rows} == {
        STANDARD_TARGET_ID,
        SECOND_STANDARD_TARGET_ID,
    }
    assert rows[0]["source_concept_name"] == "source"
    assert rows[0]["source_vocabulary_id"] == "SNOMED"
    assert rows[0]["standard_vocabulary_id"] == "SNOMED"


def test_mapping_excludes_other_relationships_and_invalid_or_non_standard_targets(
    session,
):
    _add_vocabulary_rows(session)

    rows = (
        session.execute(
            standard_concept_mapping_select(
                StandardConceptMappingSpec(source_concept_ids=(SOURCE_ID,))
            )
        )
        .mappings()
        .all()
    )

    assert len(rows) == 2


def test_mapping_returns_standard_concept_self_map(session):
    _add_vocabulary_rows(session)

    row = (
        session.execute(
            standard_concept_mapping_select(
                StandardConceptMappingSpec(source_concept_ids=(STANDARD_SOURCE_ID,))
            )
        )
        .mappings()
        .one()
    )

    assert row["source_concept_id"] == STANDARD_SOURCE_ID
    assert row["standard_concept_id"] == STANDARD_SOURCE_ID


def test_mapping_can_apply_reproducible_validity_date(session):
    _add_vocabulary_rows(session)

    rows = (
        session.execute(
            standard_concept_mapping_select(
                StandardConceptMappingSpec(
                    source_concept_ids=(SOURCE_ID,),
                    valid_on=date(2026, 1, 1),
                )
            )
        )
        .mappings()
        .all()
    )

    assert {row["standard_concept_id"] for row in rows} == {
        STANDARD_TARGET_ID,
        SECOND_STANDARD_TARGET_ID,
    }


def test_mapping_output_honours_its_documented_uniqueness_key(session):
    _add_vocabulary_rows(session)
    rows = (
        session.execute(standard_concept_mapping_select(StandardConceptMappingSpec()))
        .mappings()
        .all()
    )
    key_names = tuple(str(column) for column in STANDARD_CONCEPT_MAPPING_UNIQUENESS)
    keys = {tuple(row[name] for name in key_names) for row in rows}

    assert len(keys) == len(rows)


@pytest.mark.parametrize("dialect", [sqlite.dialect(), postgresql.dialect()])
def test_mapping_query_compiles_on_supported_dialects(dialect):
    statement = standard_concept_mapping_select(
        StandardConceptMappingSpec(
            source_concept_ids=(SOURCE_ID,),
            valid_on=date(2026, 1, 1),
        )
    )

    compiled = str(statement.compile(dialect=dialect))
    assert "concept_relationship" in compiled
    assert "mapping_source_concept" in compiled
    assert "mapping_standard_concept" in compiled
