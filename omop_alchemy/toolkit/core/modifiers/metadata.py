"""Explicit model metadata for canonical OMOP modifier queries.

The row contracts in :mod:`.contracts` describe data after projection and must
remain independent of mapped ORM models. This module owns the separate question
of which models may produce or receive those rows.

``ModifierSourceMixin`` standardises the target link as the ``modifier_of_event_id``
and ``modifier_of_field_concept_id`` hybrids
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from omop_alchemy.cdm.base import ModifierSourceMixin
from omop_alchemy.cdm.model.clinical import Measurement, Observation
from omop_alchemy.cdm.model.clinical.event_metadata import MODIFIER_TARGETS_BY_TABLE
from omop_alchemy.toolkit.core.events import (
    ClinicalEventModelSpec,
    UnsupportedClinicalEventModelError,
    clinical_event_model_spec,
)


class UnsupportedModifierSourceModelError(TypeError):
    """Raised when a model cannot provide a canonical modifier projection."""

    def __init__(self, model: object, reason: str) -> None:
        name = getattr(model, "__name__", repr(model))
        super().__init__(f"{name} is not a supported modifier model: {reason}")


class UnsupportedModifierTargetError(TypeError):
    """Raised when a model cannot be a canonical modifier target."""


@dataclass(frozen=True, slots=True)
class ModifierTargetModelSpec:
    """Native target identity and its canonical OMOP Field discriminator."""

    event_id_attribute: str
    event_field_concept_id: int
    event_source_table: str


def _source_spec(model: type[Any]) -> ClinicalEventModelSpec:
    """Resolve a source's own clinical-event metadata.

    A modifier source needs exactly the identity, date, datetime, concept and
    table that a clinical event already describes, so the event spec is used
    as-is. The modifier-flavoured renaming happens once, on the projection's
    output labels, rather than in a parallel dataclass here.
    """
    try:
        event = clinical_event_model_spec(model)
    except UnsupportedClinicalEventModelError as error:
        raise UnsupportedModifierSourceModelError(model, error.reason) from error
    if event.event_datetime_column is None:
        raise UnsupportedModifierSourceModelError(
            model, "must expose an event datetime column"
        )
    return event


CDM_MODIFIER_SOURCE_MODELS: tuple[type[Any], ...] = (Measurement, Observation)

MODIFIER_SOURCE_MODEL_SPECS_BY_TABLE: Mapping[str, ClinicalEventModelSpec] = (
    MappingProxyType(
        {
            model.__tablename__: _source_spec(model)
            for model in CDM_MODIFIER_SOURCE_MODELS
        }
    )
)


def _target_spec(target: type[Any]) -> ModifierTargetModelSpec:
    # Every field is read off the target's ModifierTargetMixin declaration, so
    # no table's identity metadata is restated here.
    return ModifierTargetModelSpec(
        event_id_attribute=target.__event_id_col__,
        event_field_concept_id=target.modifier_field_concept_id(),
        event_source_table=target.modifier_target_table(),
    )


MODIFIER_TARGET_SPECS_BY_TABLE: Mapping[str, ModifierTargetModelSpec] = (
    MappingProxyType(
        {
            table_name: _target_spec(target)
            for table_name, target in MODIFIER_TARGETS_BY_TABLE.items()
        }
    )
)


def modifier_source_model_spec(model: type[Any]) -> ClinicalEventModelSpec:
    """Resolve and validate metadata for any model declaring the modifier link."""
    if not isinstance(model, type) or not hasattr(model, "__table__"):
        raise UnsupportedModifierSourceModelError(
            model, "expected a mapped ORM model class"
        )
    if not issubclass(model, ModifierSourceMixin):
        raise UnsupportedModifierSourceModelError(
            model, "must declare the OMOP modifier link via ModifierSourceMixin"
        )
    for declaration in (
        "__modifier_event_id_col__",
        "__modifier_field_concept_id_col__",
    ):
        column_name = getattr(model, declaration, None)
        if not isinstance(column_name, str) or not column_name.strip():
            raise UnsupportedModifierSourceModelError(
                model, f"must declare {declaration}"
            )
        if not hasattr(model, column_name):
            raise UnsupportedModifierSourceModelError(
                model, f"{declaration} names a missing column: {column_name}"
            )
    spec = MODIFIER_SOURCE_MODEL_SPECS_BY_TABLE.get(
        str(getattr(model, "__tablename__", ""))
    )
    if spec is not None:
        return spec
    return _source_spec(model)


def modifier_target_model_spec(model: type[Any]) -> ModifierTargetModelSpec:
    """Resolve and validate immutable metadata for a modifier target model."""
    if not isinstance(model, type) or not hasattr(model, "__table__"):
        raise UnsupportedModifierTargetError("expected a mapped ORM model class")
    spec = MODIFIER_TARGET_SPECS_BY_TABLE.get(str(getattr(model, "__tablename__", "")))
    if spec is None:
        raise UnsupportedModifierTargetError(
            f"{model.__name__} is not a supported modifier target"
        )
    missing = tuple(
        name
        for name in ("person_id", spec.event_id_attribute)
        if not hasattr(model, name)
    )
    if missing:
        raise UnsupportedModifierTargetError(
            f"{model.__name__} is missing required attributes: {', '.join(missing)}"
        )
    return spec
