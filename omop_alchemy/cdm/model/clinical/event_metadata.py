"""Stable metadata for CDM tables that participate in clinical-event APIs.

Being a clinical event and being a valid modifier target are different
questions with different membership. Every clinical event is a valid modifier
target, but not every modifier target is a clinical event.
"""

from __future__ import annotations

from collections.abc import Callable
from types import MappingProxyType
from typing import Any, Mapping

from omop_alchemy.cdm.base import ModifierTargetMixin

from .condition_occurrence import Condition_Occurrence, Condition_OccurrenceView
from .device_exposure import Device_Exposure, Device_ExposureView
from .drug_exposure import Drug_Exposure, Drug_ExposureView
from .measurement import Measurement, MeasurementView
from .observation import Observation, ObservationView
from .procedure_occurrence import Procedure_Occurrence, Procedure_OccurrenceView
from ..structural.episode import Episode, EpisodeView


# Keep one explicit supported event set. Both lookup shapes are derived from it
# so projection and episode-resolution support cannot drift independently.
_CLINICAL_EVENT_TARGETS: tuple[tuple[type[Any], type[ModifierTargetMixin]], ...] = (
    (Condition_Occurrence, Condition_OccurrenceView),
    (Device_Exposure, Device_ExposureView),
    (Drug_Exposure, Drug_ExposureView),
    (Measurement, MeasurementView),
    (Observation, ObservationView),
    (Procedure_Occurrence, Procedure_OccurrenceView),
    # Episode is a valid modifier target, but it is not a clinical event and is
    # therefore deliberately absent from this registry.
)

_STRUCTURAL_MODIFIER_TARGETS: tuple[tuple[type[Any], type[ModifierTargetMixin]], ...] = (
    (Episode, EpisodeView),
)


def _validate_unique_target_keys(
    entries: tuple[tuple[type[Any], type[ModifierTargetMixin]], ...],
    *,
    key: Callable[[type[Any], type[ModifierTargetMixin]], object],
    label: str,
) -> None:
    """Reject duplicate target identities before a registry becomes immutable."""
    seen: dict[object, str] = {}
    for source, target in entries:
        identity = key(source, target)
        previous = seen.get(identity)
        if previous is not None:
            raise ValueError(
                f"duplicate {label} {identity!r}: {previous} and {target.__name__}"
            )
        seen[identity] = target.__name__


_ALL_MODIFIER_TARGETS = _CLINICAL_EVENT_TARGETS + _STRUCTURAL_MODIFIER_TARGETS
_validate_unique_target_keys(
    _ALL_MODIFIER_TARGETS,
    key=lambda source, _target: source.__tablename__,
    label="modifier target table",
)
_validate_unique_target_keys(
    _ALL_MODIFIER_TARGETS,
    key=lambda _source, target: target.modifier_field_concept_id(),
    label="modifier field concept ID",
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

STRUCTURAL_MODIFIER_TARGETS_BY_TABLE: Mapping[str, type[ModifierTargetMixin]] = (
    MappingProxyType(
        {source.__tablename__: target for source, target in _STRUCTURAL_MODIFIER_TARGETS}
    )
)

MODIFIER_TARGETS_BY_TABLE: Mapping[str, type[ModifierTargetMixin]] = MappingProxyType(
    {**CLINICAL_EVENT_TARGETS_BY_TABLE, **STRUCTURAL_MODIFIER_TARGETS_BY_TABLE}
)


def clinical_event_target_for_table(
    table_name: str,
) -> type[ModifierTargetMixin] | None:
    """Return the registered analytical event target for a bare CDM table."""
    return CLINICAL_EVENT_TARGETS_BY_TABLE.get(table_name)
