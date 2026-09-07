"""Deterministic modifier selection and oncology stage preferences."""

from __future__ import annotations

from datetime import date, datetime

import pytest
import sqlalchemy as sa

from omop_alchemy.toolkit.analytics.oncology import (
    DEFAULT_STAGE_SELECTION,
    GROUP_STAGE_CONCEPTS,
    M_STAGE_CONCEPTS,
    METASTATIC_DISEASE_CONCEPTS,
    N_STAGE_CONCEPTS,
    StageBasis,
    StageSelectionSpec,
    T_STAGE_CONCEPTS,
    TUMOR_GRADE_CONCEPTS,
    laterality_modifier_concept_id,
    preferred_stage_select,
    tumor_size_modifier_concept_id,
)
from omop_alchemy.toolkit.core.modifiers import (
    ModifierSelectionPolicy,
    selected_modifier_select,
)


def _stage_source(*, reverse: bool = False) -> sa.CompoundSelect:
    rows = (
        # Clinical is chronologically first, but pathological wins by default.
        (1, 10, date(2025, 1, 1), datetime(2025, 1, 1, 9), 100, "cT2"),
        (1, 11, date(2025, 2, 1), datetime(2025, 2, 1, 9), 101, "pT2"),
        (1, 12, date(2025, 3, 1), datetime(2025, 3, 1, 9), 102, "pT3"),
    )
    branches = []
    if reverse:
        rows = tuple(reversed(rows))
    for (
        person_id,
        modifier_id,
        modifier_date,
        modifier_datetime,
        concept_id,
        code,
    ) in rows:
        branches.append(
            sa.select(
                sa.literal(person_id).label("person_id"),
                sa.literal(modifier_id).label("modifier_id"),
                sa.literal(modifier_date).label("modifier_date"),
                sa.literal(modifier_datetime).label("modifier_datetime"),
                sa.literal(concept_id).label("modifier_concept_id"),
                sa.literal("measurement").label("modifier_source_table"),
                sa.literal(500).label("target_event_id"),
                sa.literal(1147127).label("target_field_concept_id"),
                sa.literal(code).label("modifier_concept_code"),
            )
        )
    return sa.union_all(*branches)


@pytest.mark.parametrize(
    ("spec", "expected_id"),
    [
        (DEFAULT_STAGE_SELECTION, 11),
        (StageSelectionSpec.clinical_first(), 10),
        (StageSelectionSpec.chronological_only(), 10),
        (
            StageSelectionSpec(
                temporal_policy=ModifierSelectionPolicy.latest,
            ),
            12,
        ),
    ],
)
def test_stage_selection_default_and_overrides(spec, expected_id: int):
    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        selected = (
            connection.execute(preferred_stage_select(_stage_source(), spec=spec))
            .mappings()
            .one()
        )

    assert selected["modifier_id"] == expected_id


def test_stage_selection_spec_is_immutable_and_validates_basis_permutation():
    with pytest.raises(AttributeError):
        DEFAULT_STAGE_SELECTION.basis_priority = (StageBasis.clinical,)  # type: ignore[misc]

    with pytest.raises(ValueError, match="each StageBasis exactly once"):
        StageSelectionSpec(basis_priority=(StageBasis.pathological,))


def test_chronological_policy_does_not_require_a_concept_code_column():
    source = _stage_source().subquery()
    without_code = sa.select(
        *(column for column in source.c if column.key != "modifier_concept_code")
    )

    preferred_stage_select(without_code, spec=StageSelectionSpec.chronological_only())


def test_same_basis_tie_is_stable_when_input_order_reverses():
    engine = sa.create_engine("sqlite://")
    selected_ids = []
    with engine.connect() as connection:
        for reverse in (False, True):
            source = _stage_source(reverse=reverse).subquery()
            tied = sa.select(source).where(source.c.modifier_id.in_((11, 12)))
            # Make both pathological candidates a true temporal tie.
            tied_source = tied.subquery()
            normalized = sa.select(
                *(
                    sa.literal(date(2025, 2, 1)).label(column.key)
                    if column.key == "modifier_date"
                    else sa.literal(datetime(2025, 2, 1, 9)).label(column.key)
                    if column.key == "modifier_datetime"
                    else column
                    for column in tied_source.c
                )
            )
            selected_ids.append(
                connection.execute(preferred_stage_select(normalized))
                .mappings()
                .one()["modifier_id"]
            )

    assert selected_ids == [11, 11]


def test_same_numeric_target_id_in_two_target_tables_forms_two_partitions():
    source = _stage_source().subquery()
    condition = sa.select(source).where(source.c.modifier_id == 10)
    procedure = sa.select(
        *(
            sa.literal(1147082).label(column.key)
            if column.key == "target_field_concept_id"
            else column
            for column in source.c
        )
    ).where(source.c.modifier_id == 11)

    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        rows = (
            connection.execute(
                selected_modifier_select(sa.union_all(condition, procedure))
            )
            .mappings()
            .all()
        )

    assert {(row["target_field_concept_id"], row["modifier_id"]) for row in rows} == {
        (1147127, 10),
        (1147082, 11),
    }


def test_condition_modifier_specs_delegate_to_governed_semantics():
    pytest.importorskip("omop_semantics")
    from omop_semantics.runtime.default_valuesets import runtime

    group_pairs = (
        (T_STAGE_CONCEPTS, runtime.staging.t_stage_concepts),
        (N_STAGE_CONCEPTS, runtime.staging.n_stage_concepts),
        (M_STAGE_CONCEPTS, runtime.staging.m_stage_concepts),
        (GROUP_STAGE_CONCEPTS, runtime.staging.group_stage_concepts),
        (
            METASTATIC_DISEASE_CONCEPTS,
            runtime.condition_modifiers.metastatic_disease_concepts,
        ),
    )
    for spec, unit in group_pairs:
        assert set(spec.parent_ids()) == set(unit.parent_ids)
    assert set(TUMOR_GRADE_CONCEPTS.exact_ids()) == set(
        runtime.condition_modifiers.tumor_grade.exact_ids
    )
    assert (
        laterality_modifier_concept_id()
        == runtime.condition_modifiers.condition_modifier_values.laterality
    )
    assert (
        tumor_size_modifier_concept_id()
        == runtime.condition_modifiers.numeric_condition_modifiers.tumor_size
    )
