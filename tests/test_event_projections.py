"""Canonical clinical-event projection behaviour."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest
from sqlalchemy.dialects import postgresql, sqlite

from omop_alchemy.cdm.base import ModifierFieldConcepts
from omop_alchemy.cdm.model import (
    Condition_Occurrence,
    Device_Exposure,
    Drug_Exposure,
    Measurement,
    Observation,
    Procedure_Occurrence,
)
from omop_alchemy.cdm.model.clinical import (
    Condition_OccurrenceView,
    Drug_ExposureView,
    MeasurementView,
    ObservationView,
    Procedure_OccurrenceView,
)
from omop_alchemy.cdm.base import ModifierTargetMixin
from omop_alchemy.cdm.model.structural import Episode_EventView
from omop_alchemy.toolkit.core.events import (
    CANONICAL_EVENT_OPTIONAL_COLUMNS,
    CANONICAL_EVENT_REQUIRED_COLUMNS,
    UnsupportedClinicalEventModelError,
    canonical_event_projection,
    canonical_event_union,
    clinical_event_model_spec,
)


@pytest.mark.parametrize(
    ("model", "source_table", "field_concept_id"),
    [
        (
            Condition_Occurrence,
            "condition_occurrence",
            ModifierFieldConcepts.CONDITION_OCCURRENCE,
        ),
        (Drug_Exposure, "drug_exposure", ModifierFieldConcepts.DRUG_EXPOSURE),
        (Measurement, "measurement", ModifierFieldConcepts.MEASUREMENT),
        (Observation, "observation", ModifierFieldConcepts.OBSERVATION),
        (
            Procedure_Occurrence,
            "procedure_occurrence",
            ModifierFieldConcepts.PROCEDURE_OCCURRENCE,
        ),
    ],
)
def test_projection_resolves_source_metadata(
    model,
    source_table: str,
    field_concept_id: int,
):
    spec = clinical_event_model_spec(model)
    statement = canonical_event_projection(model)

    assert spec.event_source_table == source_table
    assert spec.event_field_concept_id == field_concept_id
    assert tuple(statement.selected_columns.keys()) == tuple(
        map(str, CANONICAL_EVENT_REQUIRED_COLUMNS + CANONICAL_EVENT_OPTIONAL_COLUMNS)
    )


@pytest.mark.parametrize("dialect", [sqlite.dialect(), postgresql.dialect()])
def test_projection_compiles_discriminator_and_source_as_literals(dialect):
    statement = canonical_event_projection(Measurement)
    compiled = str(
        statement.compile(dialect=dialect, compile_kwargs={"literal_binds": True})
    )

    assert str(ModifierFieldConcepts.MEASUREMENT) in compiled
    assert "'measurement'" in compiled
    assert "measurement.value_as_number AS value_as_number" in compiled


def test_non_value_event_projects_typed_null_value_columns():
    compiled = str(
        canonical_event_projection(Procedure_Occurrence).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "CAST(NULL AS FLOAT) AS value_as_number" in compiled
    assert "CAST(NULL AS INTEGER) AS value_as_concept_id" in compiled


def test_projection_union_preserves_one_shared_shape():
    statement = canonical_event_union(
        Measurement,
        Observation,
        Procedure_Occurrence,
    )
    compiled = str(statement.compile(dialect=sqlite.dialect()))

    assert tuple(statement.selected_columns.keys()) == tuple(
        map(str, CANONICAL_EVENT_REQUIRED_COLUMNS + CANONICAL_EVENT_OPTIONAL_COLUMNS)
    )
    assert compiled.count("UNION ALL") == 2


def test_incomplete_modifier_target_has_a_typed_error():
    with pytest.raises(
        UnsupportedClinicalEventModelError,
        match="no complete ModifierTargetMixin metadata",
    ):
        canonical_event_projection(Device_Exposure)


def test_all_core_event_views_are_registered_episode_event_targets():
    targets = Episode_EventView.resolved_event_target_classes()

    expected = {
        ModifierFieldConcepts.CONDITION_OCCURRENCE: Condition_OccurrenceView,
        ModifierFieldConcepts.DRUG_EXPOSURE: Drug_ExposureView,
        ModifierFieldConcepts.MEASUREMENT: MeasurementView,
        ModifierFieldConcepts.OBSERVATION: ObservationView,
        ModifierFieldConcepts.PROCEDURE_OCCURRENCE: Procedure_OccurrenceView,
    }

    assert {field: targets[field] for field in expected} == expected
    assert not issubclass(Measurement, ModifierTargetMixin)
    assert not issubclass(Observation, ModifierTargetMixin)


def test_registered_event_views_have_distinct_field_concepts():
    views = (
        Condition_OccurrenceView,
        Drug_ExposureView,
        MeasurementView,
        ObservationView,
        Procedure_OccurrenceView,
    )
    field_concepts = tuple(view.modifier_field_concept_id() for view in views)

    assert len(field_concepts) == len(set(field_concepts)) == 5


def test_analytics_import_preserves_all_core_metadata_and_compiled_projections():
    code = textwrap.dedent(
        """
        from sqlalchemy.dialects import postgresql, sqlite
        from omop_alchemy.cdm.model import (
            Condition_Occurrence,
            Drug_Exposure,
            Measurement,
            Observation,
            Procedure_Occurrence,
        )
        from omop_alchemy.toolkit.core.events import (
            canonical_event_projection,
            clinical_event_model_spec,
        )

        models = (
            Condition_Occurrence,
            Drug_Exposure,
            Measurement,
            Observation,
            Procedure_Occurrence,
        )

        def snapshot():
            return tuple(
                (
                    model.__name__,
                    clinical_event_model_spec(model),
                    str(canonical_event_projection(model).compile(dialect=sqlite.dialect())),
                    str(canonical_event_projection(model).compile(dialect=postgresql.dialect())),
                )
                for model in models
            )

        before = snapshot()
        import omop_alchemy.toolkit.analytics.oncology
        assert snapshot() == before
        """
    )

    subprocess.run([sys.executable, "-c", code], check=True)
