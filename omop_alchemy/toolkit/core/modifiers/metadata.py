"""Explicit model metadata for canonical OMOP modifier queries.

The row contracts in :mod:`.contracts` describe data after projection and must
remain independent of mapped ORM models. This module owns the separate question
of which models may produce or receive those rows.

Two principles keep these registries small and predictable:

* support is explicit and immutable; mapper discovery and subclass walks would
  make accepted models depend on import order; and
* metadata already governed by the clinical-event layer is derived from that
  layer rather than copied here.

Measurement and Observation are the only initial modifier sources. Their own
event identity, date, datetime, concept, and source-table metadata comes from
``clinical_event_model_spec``. Both models already expose the target link using
the common ``modifier_of_event_id`` and ``modifier_of_field_concept_id`` hybrid
attributes, so this registry does not repeat their different physical column
names.

The six clinical modifier targets are derived from the stable CDM clinical-event
registry. Episode is the sole explicit extension because it is a valid OMOP
modifier target but deliberately is not a clinical-event projection.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from omop_alchemy.cdm.base import ModifierFieldConcepts
from omop_alchemy.cdm.model.clinical import Measurement, Observation
from omop_alchemy.cdm.model.clinical.event_metadata import (
    CLINICAL_EVENT_TARGETS_BY_TABLE,
)
from omop_alchemy.cdm.model.structural import Episode
from omop_alchemy.toolkit.core.events import clinical_event_model_spec


MODIFIER_TARGET_EVENT_ATTRIBUTE = "modifier_of_event_id"
MODIFIER_TARGET_FIELD_ATTRIBUTE = "modifier_of_field_concept_id"


class UnsupportedModifierSourceModelError(TypeError):
    """Raised when a model cannot provide a canonical modifier projection."""

    def __init__(self, model: object, reason: str) -> None:
        name = getattr(model, "__name__", repr(model))
        super().__init__(f"{name} is not a supported modifier model: {reason}")


class UnsupportedModifierTargetError(TypeError):
    """Raised when a model cannot be a canonical modifier target."""


@dataclass(frozen=True, slots=True)
class ModifierSourceModelSpec:
    """Native model attributes used to emit one canonical modifier row."""

    modifier_id_attribute: str
    modifier_date_attribute: str
    modifier_datetime_attribute: str
    modifier_concept_id_attribute: str
    target_event_id_attribute: str
    target_field_concept_id_attribute: str
    modifier_source_table: str


@dataclass(frozen=True, slots=True)
class ModifierTargetModelSpec:
    """Native target identity and its canonical OMOP Field discriminator."""

    event_id_attribute: str
    event_field_concept_id: int
    event_source_table: str
    uses_clinical_event_projection: bool


def _source_spec(model: type[Any]) -> ModifierSourceModelSpec:
    """Derive shared source fields while retaining an explicit support list."""
    event = clinical_event_model_spec(model)
    if event.event_datetime_column is None:
        raise TypeError(f"{model.__name__} must expose an event datetime column")
    return ModifierSourceModelSpec(
        modifier_id_attribute=event.event_id_column,
        modifier_date_attribute=event.event_date_column,
        modifier_datetime_attribute=event.event_datetime_column,
        modifier_concept_id_attribute=event.event_concept_id_column,
        target_event_id_attribute=MODIFIER_TARGET_EVENT_ATTRIBUTE,
        target_field_concept_id_attribute=MODIFIER_TARGET_FIELD_ATTRIBUTE,
        modifier_source_table=event.event_source_table,
    )


# This tuple is the deliberate modifier-source allow-list. The detailed column
# metadata is derived rather than restated below.
SUPPORTED_MODIFIER_MODELS: tuple[type[Any], ...] = (Measurement, Observation)

MODIFIER_SOURCE_MODEL_SPECS_BY_TABLE: Mapping[str, ModifierSourceModelSpec] = (
    MappingProxyType(
        {
            model.__tablename__: _source_spec(model)
            for model in SUPPORTED_MODIFIER_MODELS
        }
    )
)


def _clinical_target_spec(target: type[Any]) -> ModifierTargetModelSpec:
    return ModifierTargetModelSpec(
        event_id_attribute=target.__event_id_col__,
        event_field_concept_id=target.modifier_field_concept_id(),
        event_source_table=target.modifier_target_table(),
        uses_clinical_event_projection=True,
    )


# Clinical target definitions remain single-sourced in event_metadata. Episode
# extends that surface explicitly without being registered as a clinical event.
MODIFIER_TARGET_SPECS_BY_TABLE: Mapping[str, ModifierTargetModelSpec] = (
    MappingProxyType(
        {
            **{
                table_name: _clinical_target_spec(target)
                for table_name, target in CLINICAL_EVENT_TARGETS_BY_TABLE.items()
            },
            Episode.__tablename__: ModifierTargetModelSpec(
                event_id_attribute="episode_id",
                event_field_concept_id=ModifierFieldConcepts.EPISODE,
                event_source_table=Episode.__tablename__,
                uses_clinical_event_projection=False,
            ),
        }
    )
)


def modifier_source_model_spec(model: type[Any]) -> ModifierSourceModelSpec:
    """Resolve and validate immutable metadata for a modifier source model."""
    if not isinstance(model, type) or not hasattr(model, "__table__"):
        raise UnsupportedModifierSourceModelError(
            model, "expected a mapped ORM model class"
        )
    spec = MODIFIER_SOURCE_MODEL_SPECS_BY_TABLE.get(
        str(getattr(model, "__tablename__", ""))
    )
    if spec is None:
        raise UnsupportedModifierSourceModelError(
            model, "only Measurement and Observation carry the OMOP modifier link"
        )
    required = (
        "person_id",
        spec.modifier_id_attribute,
        spec.modifier_date_attribute,
        spec.modifier_datetime_attribute,
        spec.modifier_concept_id_attribute,
        spec.target_event_id_attribute,
        spec.target_field_concept_id_attribute,
    )
    missing = tuple(name for name in required if not hasattr(model, name))
    if missing:
        raise UnsupportedModifierSourceModelError(
            model, f"missing required attributes: {', '.join(missing)}"
        )
    return spec


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
