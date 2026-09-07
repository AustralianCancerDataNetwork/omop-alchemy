"""Canonical modifier projection and target-resolution contracts."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
import sqlalchemy.orm as so
from datetime import date, datetime
from sqlalchemy.dialects import postgresql, sqlite

from omop_alchemy.cdm.base import (
    ModifierFieldConcepts,
    ModifierSourceMixin,
    ModifierTargetMixin,
)
from omop_alchemy.cdm.model import (
    Condition_Occurrence,
    Device_Exposure,
    Drug_Exposure,
    Measurement,
    Observation,
    Person,
    Procedure_Occurrence,
)
from omop_alchemy.cdm.model.structural import Episode, Episode_EventView
from omop_alchemy.cdm.model.clinical.event_metadata import (
    CLINICAL_EVENT_TARGETS_BY_FIELD_CONCEPT_ID,
    CLINICAL_EVENT_TARGETS_BY_TABLE,
    MODIFIER_TARGETS_BY_TABLE,
    STRUCTURAL_MODIFIER_TARGETS_BY_TABLE,
    clinical_event_target_for_table,
)
from omop_alchemy.toolkit.core.events import ClinicalEventModelSpec
from omop_alchemy.toolkit.core.modifiers.projections import _VALUE_COLUMN_TYPES
from omop_alchemy.toolkit.core.modifiers.contracts import (
    ModifierColumn,
    ModifierRow,
    ValuedModifierRow,
)
from omop_alchemy.toolkit.core.modifiers import (
    CANONICAL_MODIFIER_REQUIRED_COLUMNS,
    CANONICAL_MODIFIER_VALUE_COLUMNS,
    CDM_MODIFIER_SOURCE_MODELS,
    MODIFIER_SOURCE_MODEL_SPECS_BY_TABLE,
    MODIFIER_TARGET_SPECS_BY_TABLE,
    UnsupportedModifierSourceModelError,
    canonical_modifier_projection,
    canonical_modifier_target_projection,
    canonical_modifier_union,
    modifier_source_model_spec,
    modifier_target_model_spec,
    modifier_target_queries,
)


# The full projection shape, in UNION position order. Only the tests need the
# whole; production code asks for the obligation it actually cares about.
_ALL_MODIFIER_COLUMNS = (
    CANONICAL_MODIFIER_REQUIRED_COLUMNS + CANONICAL_MODIFIER_VALUE_COLUMNS
)


@pytest.mark.parametrize(
    ("model", "source_table"),
    [(Measurement, "measurement"), (Observation, "observation")],
)
def test_modifier_projection_has_one_stable_shape(model, source_table: str):
    statement = canonical_modifier_projection(model)

    assert tuple(statement.selected_columns.keys()) == tuple(
        map(str, _ALL_MODIFIER_COLUMNS)
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
        map(str, _ALL_MODIFIER_COLUMNS)
    )
    assert "UNION ALL" in str(statement.compile(dialect=sqlite.dialect()))


def test_non_modifier_model_fails_at_query_construction():
    with pytest.raises(
        UnsupportedModifierSourceModelError, match="ModifierSourceMixin"
    ):
        canonical_modifier_projection(Person)


def test_modifier_metadata_reuses_generic_model_interfaces():
    # The target link is no longer described by the spec at all; it is read off
    # ModifierSourceMixin, which is asserted separately.
    assert set(MODIFIER_SOURCE_MODEL_SPECS_BY_TABLE) == {"measurement", "observation"}
    for table, spec in MODIFIER_SOURCE_MODEL_SPECS_BY_TABLE.items():
        # A source spec IS the clinical-event spec; the modifier-flavoured
        # renaming happens on the projection's output labels, not here.
        assert isinstance(spec, ClinicalEventModelSpec)
        assert spec.event_source_table == table


def test_modifier_target_surface_is_the_clinical_and_structural_registries():
    assert set(MODIFIER_TARGETS_BY_TABLE) == {
        *CLINICAL_EVENT_TARGETS_BY_TABLE,
        *STRUCTURAL_MODIFIER_TARGETS_BY_TABLE,
    }
    assert not set(CLINICAL_EVENT_TARGETS_BY_TABLE) & set(
        STRUCTURAL_MODIFIER_TARGETS_BY_TABLE
    )
    assert set(STRUCTURAL_MODIFIER_TARGETS_BY_TABLE) == {"episode"}


def test_structural_groupers_never_enter_the_clinical_event_registry():
    """Episode groups clinical events, so it must never be resolvable as one.

    Admitting it would let an ``episode_event`` row resolve to an Episode, so a
    grouper would appear inside its own ``EpisodeView.events`` list, and would
    let canonical event projections double-count a grouper alongside the events
    it groups.
    """
    for table_name in STRUCTURAL_MODIFIER_TARGETS_BY_TABLE:
        assert table_name not in CLINICAL_EVENT_TARGETS_BY_TABLE
        assert clinical_event_target_for_table(table_name) is None

    structural_field_concepts = {
        target.modifier_field_concept_id()
        for target in STRUCTURAL_MODIFIER_TARGETS_BY_TABLE.values()
    }
    assert not structural_field_concepts & set(
        CLINICAL_EVENT_TARGETS_BY_FIELD_CONCEPT_ID
    )
    assert not structural_field_concepts & set(
        Episode_EventView.resolved_event_target_classes()
    )


def test_canonical_column_vocabulary_is_stated_once():
    """The enum, the obligation tuples, and the row protocols must agree.

    Each names the same columns for a different audience, and nothing in the
    language keeps them in step, so the agreement is asserted here.
    """
    assert not set(CANONICAL_MODIFIER_REQUIRED_COLUMNS) & set(
        CANONICAL_MODIFIER_VALUE_COLUMNS
    ), "a column cannot be both required and an optional value"
    assert set(_ALL_MODIFIER_COLUMNS) == set(ModifierColumn), (
        "every ModifierColumn must be classified as required or value"
    )
    assert len(_ALL_MODIFIER_COLUMNS) == len(ModifierColumn)

    assert tuple(ModifierRow.__annotations__) == tuple(
        map(str, CANONICAL_MODIFIER_REQUIRED_COLUMNS)
    )
    assert tuple(ValuedModifierRow.__annotations__) == tuple(
        map(str, CANONICAL_MODIFIER_VALUE_COLUMNS)
    )

    # The projection casts each value position when a source lacks the column,
    # so every value column needs a declared SQL type to fall back to.
    assert set(_VALUE_COLUMN_TYPES) == set(CANONICAL_MODIFIER_VALUE_COLUMNS)


def test_modifier_sources_declare_the_link_through_the_source_mixin():
    """The common vocabulary must resolve to each table's own physical columns."""
    expected = {
        Measurement: ("measurement_event_id", "meas_event_field_concept_id"),
        Observation: ("observation_event_id", "obs_event_field_concept_id"),
    }
    for model, (event_column, field_column) in expected.items():
        assert issubclass(model, ModifierSourceMixin)
        assert model.modifier_source_table() == model.__tablename__
        for hybrid, column_name in (
            (model.modifier_of_event_id, event_column),
            (model.modifier_of_field_concept_id, field_column),
        ):
            # The hybrid renders as an annotated form of the mapped column, so
            # compare semantically rather than by object identity.
            rendered = hybrid.__clause_element__()
            assert rendered.compare(model.__table__.c[column_name])
            assert rendered.table is model.__table__


def _make_custom_source(**overrides):
    """Build a mapped modifier source outside the CDM, on its own registry."""

    class LocalBase(so.DeclarativeBase):
        pass

    class CustomSource(LocalBase, ModifierSourceMixin, ModifierTargetMixin):
        __tablename__ = "custom_source"
        __event_id_col__ = "custom_source_id"
        __concept_id_col__ = "custom_concept_id"
        __start_date_col__ = "custom_date"
        __end_date_col__ = "custom_date"
        __type_concept_id_col__ = "custom_concept_id"
        __modifier_event_id_col__ = overrides.get(
            "__modifier_event_id_col__", "custom_event_id"
        )
        __modifier_field_concept_id_col__ = "custom_event_field_concept_id"

        custom_source_id: so.Mapped[int] = so.mapped_column(primary_key=True)
        person_id: so.Mapped[int]
        custom_concept_id: so.Mapped[int]
        custom_date: so.Mapped[date]
        custom_datetime: so.Mapped[datetime | None]
        custom_event_id: so.Mapped[int | None]
        custom_event_field_concept_id: so.Mapped[int | None]

        @classmethod
        def modifier_field_concept_id(cls) -> int:
            return 999999

    return CustomSource


def test_a_new_modifier_source_needs_no_change_to_the_toolkit():
    """Support is a property of the model, not a list held in this package."""
    custom = _make_custom_source()

    assert custom not in CDM_MODIFIER_SOURCE_MODELS
    assert tuple(canonical_modifier_projection(custom).selected_columns.keys()) == (
        tuple(map(str, _ALL_MODIFIER_COLUMNS))
    )

    spec = modifier_source_model_spec(custom)
    assert spec.event_source_table == "custom_source"
    assert spec.event_id_column == "custom_source_id"

    union = canonical_modifier_union(Measurement, custom)
    assert "UNION ALL" in str(union.compile(dialect=sqlite.dialect()))


def test_a_source_naming_a_missing_link_column_is_rejected():
    """The mixin supplies the hybrids; the subclass must name real columns."""
    custom = _make_custom_source(__modifier_event_id_col__="no_such_column")

    with pytest.raises(
        UnsupportedModifierSourceModelError, match="names a missing column"
    ):
        modifier_source_model_spec(custom)


def test_modifier_targets_derive_the_clinical_surface_and_extend_it_with_episode():
    assert set(MODIFIER_TARGET_SPECS_BY_TABLE) == {
        *CLINICAL_EVENT_TARGETS_BY_TABLE,
        "episode",
    }
    assert all(
        MODIFIER_TARGET_SPECS_BY_TABLE[table].event_source_table == table
        for table in MODIFIER_TARGET_SPECS_BY_TABLE
    )


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
