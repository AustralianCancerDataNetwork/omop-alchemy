"""Stable metadata for CDM tables that participate in clinical-event APIs."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

from omop_alchemy.cdm.base import ModifierTargetMixin

from .condition_occurrence import Condition_Occurrence, Condition_OccurrenceView
from .device_exposure import Device_Exposure, Device_ExposureView
from .drug_exposure import Drug_Exposure, Drug_ExposureView
from .measurement import Measurement, MeasurementView
from .observation import Observation, ObservationView
from .procedure_occurrence import Procedure_Occurrence, Procedure_OccurrenceView


# Keep one explicit supported event set. Both lookup shapes are derived from it
# so projection and episode-resolution support cannot drift independently.
_CLINICAL_EVENT_TARGETS: tuple[tuple[type[Any], type[ModifierTargetMixin]], ...] = (
    (Condition_Occurrence, Condition_OccurrenceView),
    (Device_Exposure, Device_ExposureView),
    (Drug_Exposure, Drug_ExposureView),
    (Measurement, MeasurementView),
    (Observation, ObservationView),
    (Procedure_Occurrence, Procedure_OccurrenceView),
)

CLINICAL_EVENT_TARGETS_BY_TABLE: Mapping[str, type[ModifierTargetMixin]] = (
    MappingProxyType(
        {source.__tablename__: target for source, target in _CLINICAL_EVENT_TARGETS}
    )
)
CLINICAL_EVENT_TARGETS_BY_FIELD_CONCEPT_ID: Mapping[int, type[ModifierTargetMixin]] = (
    MappingProxyType(
        {
            target.modifier_field_concept_id(): target
            for _, target in _CLINICAL_EVENT_TARGETS
        }
    )
)


def clinical_event_target_for_table(
    table_name: str,
) -> type[ModifierTargetMixin] | None:
    """Return the analytical target that owns metadata for a bare CDM table."""
    return CLINICAL_EVENT_TARGETS_BY_TABLE.get(table_name)
