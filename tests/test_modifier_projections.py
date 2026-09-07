"""Canonical modifier projection and target-resolution contracts."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite

from omop_alchemy.cdm.base import ModifierFieldConcepts
from omop_alchemy.cdm.model import (
    Condition_Occurrence,
    Device_Exposure,
    Drug_Exposure,
    Measurement,
    Observation,
    Person,
    Procedure_Occurrence,
)
from omop_alchemy.cdm.model.structural import Episode
from omop_alchemy.cdm.model.clinical.event_metadata import (
    CLINICAL_EVENT_TARGETS_BY_TABLE,
)
from omop_alchemy.toolkit.core.modifiers import (
    CANONICAL_MODIFIER_REQUIRED_COLUMNS,
    CANONICAL_MODIFIER_VALUE_COLUMNS,
    MODIFIER_SOURCE_MODEL_SPECS_BY_TABLE,
    MODIFIER_TARGET_SPECS_BY_TABLE,
    UnsupportedModifierSourceModelError,
    canonical_modifier_projection,
    canonical_modifier_target_projection,
    canonical_modifier_union,
    modifier_target_model_spec,
    modifier_target_queries,
)


@pytest.mark.parametrize(
    ("model", "source_table"),
    [(Measurement, "measurement"), (Observation, "observation")],
)
def test_modifier_projection_has_one_stable_shape(model, source_table: str):
    statement = canonical_modifier_projection(model)

    assert tuple(statement.selected_columns.keys()) == tuple(
        map(
            str,
            CANONICAL_MODIFIER_REQUIRED_COLUMNS + CANONICAL_MODIFIER_VALUE_COLUMNS,
        )
    )
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert f"'{source_table}' AS modifier_source_table" in compiled


def test_modifier_union_preserves_shape_and_all_rows():
    statement = canonical_modifier_union(Measurement, Observation)

    assert tuple(statement.selected_columns.keys()) == tuple(
        map(
            str,
            CANONICAL_MODIFIER_REQUIRED_COLUMNS + CANONICAL_MODIFIER_VALUE_COLUMNS,
        )
    )
    assert "UNION ALL" in str(statement.compile(dialect=sqlite.dialect()))


def test_non_modifier_model_fails_at_query_construction():
    with pytest.raises(UnsupportedModifierSourceModelError, match="only Measurement"):
        canonical_modifier_projection(Person)


def test_modifier_metadata_reuses_generic_model_interfaces():
    assert set(MODIFIER_SOURCE_MODEL_SPECS_BY_TABLE) == {"measurement", "observation"}
    for spec in MODIFIER_SOURCE_MODEL_SPECS_BY_TABLE.values():
        assert spec.target_event_id_attribute == "modifier_of_event_id"
        assert spec.target_field_concept_id_attribute == (
            "modifier_of_field_concept_id"
        )


def test_modifier_targets_derive_the_clinical_surface_and_extend_it_with_episode():
    assert set(MODIFIER_TARGET_SPECS_BY_TABLE) == {
        *CLINICAL_EVENT_TARGETS_BY_TABLE,
        "episode",
    }
    assert all(
        MODIFIER_TARGET_SPECS_BY_TABLE[table].uses_clinical_event_projection
        for table in CLINICAL_EVENT_TARGETS_BY_TABLE
    )
    assert not MODIFIER_TARGET_SPECS_BY_TABLE["episode"].uses_clinical_event_projection


def test_episode_is_a_modifier_target_without_becoming_a_clinical_event():
    spec = modifier_target_model_spec(Episode)
    projection = canonical_modifier_target_projection(Episode)

    assert spec.event_field_concept_id == ModifierFieldConcepts.EPISODE
    assert tuple(projection.selected_columns.keys()) == (
        "person_id",
        "event_id",
        "event_field_concept_id",
        "event_source_table",
    )


@pytest.mark.parametrize(
    "target",
    [
        Condition_Occurrence,
        Device_Exposure,
        Drug_Exposure,
        Measurement,
        Observation,
        Procedure_Occurrence,
        Episode,
    ],
)
def test_target_resolution_queries_compile_for_supported_models(target):
    queries = modifier_target_queries(
        Measurement, target, include_unmatched=True, diagnostics=True
    )

    assert queries.diagnostics is not None
    for dialect in (sqlite.dialect(), postgresql.dialect()):
        assert "resolved_target_event_id" in str(
            queries.matches.compile(dialect=dialect)
        )
        assert "diagnostic_code" in str(queries.diagnostics.compile(dialect=dialect))


def test_target_validation_rejects_missing_and_cross_person_links():
    def modifier(
        modifier_id: int,
        person_id: int,
        target_id: int | None,
        target_field: int | None,
    ):
        return sa.select(
            sa.literal(person_id).label("person_id"),
            sa.literal(modifier_id).label("modifier_id"),
            sa.literal("2025-01-01").label("modifier_date"),
            sa.cast(sa.null(), sa.DateTime()).label("modifier_datetime"),
            sa.literal(100).label("modifier_concept_id"),
            sa.literal("measurement").label("modifier_source_table"),
            sa.literal(target_id).label("target_event_id"),
            sa.literal(target_field).label("target_field_concept_id"),
        )

    modifiers = sa.union_all(
        modifier(1, 10, 7, ModifierFieldConcepts.CONDITION_OCCURRENCE),
        modifier(2, 10, None, None),
        modifier(3, 10, 8, ModifierFieldConcepts.CONDITION_OCCURRENCE),
        modifier(4, 11, 7, ModifierFieldConcepts.CONDITION_OCCURRENCE),
    )
    targets = sa.select(
        sa.literal(10).label("person_id"),
        sa.literal(7).label("event_id"),
        sa.literal(ModifierFieldConcepts.CONDITION_OCCURRENCE).label(
            "event_field_concept_id"
        ),
        sa.literal("condition_occurrence").label("event_source_table"),
    )
    queries = modifier_target_queries(modifiers, targets, diagnostics=True)

    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        matches = connection.execute(queries.matches).mappings().all()
        assert queries.diagnostics is not None
        diagnostics = connection.execute(queries.diagnostics).mappings().all()

    assert [row["modifier_id"] for row in matches] == [1]
    assert {(row["modifier_id"], row["diagnostic_code"]) for row in diagnostics} == {
        (2, "missing_target_identity"),
        (3, "missing_target_event"),
        (4, "person_mismatch"),
    }
