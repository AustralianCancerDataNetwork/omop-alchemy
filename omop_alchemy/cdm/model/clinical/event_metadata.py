"""Stable metadata for CDM tables that participate in clinical-event APIs.

Being a clinical event and being a valid modifier target are different
questions with different membership. Every clinical event is a valid modifier
target, but not every modifier target is a clinical event.
"""

from __future__ import annotations

from collections.abc import Callable
from types import MappingProxyType
from typing import Any, Mapping

from omop_alchemy.cdm.base import (
    ClinicalEventMixin,
    ModifierSourceMixin,
    ModifierTargetMixin,
)
from omop_alchemy.cdm.base.event_metadata import (
    ClinicalEventModelSpec,
    UnsupportedClinicalEventModelError,
)

from .condition_occurrence import Condition_Occurrence, Condition_OccurrenceView
from .device_exposure import Device_Exposure, Device_ExposureView
from .drug_exposure import Drug_Exposure, Drug_ExposureView
from .measurement import Measurement, MeasurementView
from .observation import Observation, ObservationView
from .procedure_occurrence import Procedure_Occurrence, Procedure_OccurrenceView
from ..structural.episode import Episode, EpisodeView


# Keep one explicit supported event set. Both lookup shapes are derived from it
# so projection and episode-resolution support cannot drift independently.
_CLINICAL_EVENT_TARGETS: tuple[tuple[type[Any], type[ClinicalEventMixin]], ...] = (
    (Condition_Occurrence, Condition_OccurrenceView),
    (Device_Exposure, Device_ExposureView),
    (Drug_Exposure, Drug_ExposureView),
    (Measurement, MeasurementView),
    (Observation, ObservationView),
    (Procedure_Occurrence, Procedure_OccurrenceView),
    # Episode is a valid modifier target, but it is not a clinical event and is
    # therefore deliberately absent from this registry.
)

_STRUCTURAL_MODIFIER_TARGETS: tuple[
    tuple[type[Any], type[ModifierTargetMixin]], ...
] = ((Episode, EpisodeView),)


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

CLINICAL_EVENT_TARGETS_BY_TABLE: Mapping[str, type[ClinicalEventMixin]] = (
    MappingProxyType(
        {source.__tablename__: target for source, target in _CLINICAL_EVENT_TARGETS}
    )
)
CLINICAL_EVENT_TARGETS_BY_FIELD_CONCEPT_ID: Mapping[int, type[ClinicalEventMixin]] = (
    MappingProxyType(
        {
            target.modifier_field_concept_id(): target
            for _, target in _CLINICAL_EVENT_TARGETS
        }
    )
)

STRUCTURAL_MODIFIER_TARGETS_BY_TABLE: Mapping[str, type[ModifierTargetMixin]] = (
    MappingProxyType(
        {
            source.__tablename__: target
            for source, target in _STRUCTURAL_MODIFIER_TARGETS
        }
    )
)

MODIFIER_TARGETS_BY_TABLE: Mapping[str, type[ModifierTargetMixin]] = MappingProxyType(
    {**CLINICAL_EVENT_TARGETS_BY_TABLE, **STRUCTURAL_MODIFIER_TARGETS_BY_TABLE}
)


def clinical_event_target_for_table(
    table_name: str,
) -> type[ClinicalEventMixin] | None:
    """Return the registered analytical event target for a bare CDM table."""
    return CLINICAL_EVENT_TARGETS_BY_TABLE.get(table_name)


def _metadata_candidate(model: type[Any]) -> type[ClinicalEventMixin] | None:
    # An explicitly supplied registered event view (or its domain-specific
    # subclass) owns its metadata. Bare CDM tables use the registered CDM view
    # for that table; unrelated subclasses are never discovered by walking
    # Python's import-dependent subclass graph.
    if issubclass(model, ModifierTargetMixin):
        if (
            not issubclass(model, ClinicalEventMixin)
            or not model.has_complete_metadata()
        ):
            return None
        table_name = getattr(model, "__tablename__", None)
        registered_view = clinical_event_target_for_table(str(table_name))
        if registered_view is not None and issubclass(model, registered_view):
            return model
        # Modifier sources outside the built-in event set are intentionally
        # supported by the canonical modifier projection. They carry the same
        # event-shaped metadata, but do not become episode-resolvable events.
        if issubclass(model, ModifierSourceMixin):
            return model
        return None
    # Lean CDM models intentionally do not carry modifier metadata. Resolve
    # their table through the configured analytical view without changing the
    # class used to read scalar event rows.
    table_name = getattr(model, "__tablename__", None)
    return clinical_event_target_for_table(str(table_name))


def clinical_event_model_spec(model: type[Any]) -> ClinicalEventModelSpec:
    """Resolve the event metadata for an ORM model without accessing a database."""
    # Resolve metadata before building SQL so unsupported models fail at query
    # construction, rather than producing a partially shaped union at runtime.
    if not isinstance(model, type) or not hasattr(model, "__table__"):
        raise UnsupportedClinicalEventModelError(
            model, "expected a mapped ORM model class"
        )

    metadata_model = _metadata_candidate(model)
    if metadata_model is None:
        raise UnsupportedClinicalEventModelError(
            model,
            "no complete ClinicalEventMixin metadata is available",
        )

    return metadata_model.clinical_event_model_spec(model)
