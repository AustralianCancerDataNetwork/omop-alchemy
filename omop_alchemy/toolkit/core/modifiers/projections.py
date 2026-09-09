"""Canonical SQLAlchemy projections for OMOP modifier-bearing tables."""

from __future__ import annotations

from typing import Any, Mapping

import sqlalchemy as sa

from omop_alchemy.toolkit._utils import _nullable_column, _select_or_union_all

from .contracts import CANONICAL_MODIFIER_VALUE_COLUMNS, ModifierColumn
from .metadata import (
    UnsupportedModifierSourceModelError,
    modifier_source_model_spec,
)


# The SQL type each value position is cast to when a source has no such column.
# Keyed by column so the emission order below comes from the contract rather
# than from a second hand-maintained list.
_VALUE_COLUMN_TYPES: Mapping[ModifierColumn, sa.types.TypeEngine[Any]] = {
    ModifierColumn.value_as_number: sa.Float(),
    ModifierColumn.value_as_concept_id: sa.Integer(),
    ModifierColumn.unit_concept_id: sa.Integer(),
    ModifierColumn.value_as_string: sa.String(),
}


def canonical_modifier_projection(
    model: type[Any], *, include_values: bool = True
) -> sa.Select[Any]:
    """Project Measurement or Observation into one modifier row shape."""
    spec = modifier_source_model_spec(model)
    datetime_column = spec.event_datetime_column
    if datetime_column is None:  # pragma: no cover - the spec rejects such a model
        raise UnsupportedModifierSourceModelError(
            model, "must expose an event datetime column"
        )
    columns: list[sa.ColumnElement[Any]] = [
        model.person_id.label(str(ModifierColumn.person_id)),
        getattr(model, spec.event_id_column).label(str(ModifierColumn.modifier_id)),
        getattr(model, spec.event_date_column).label(str(ModifierColumn.modifier_date)),
        getattr(model, datetime_column).label(str(ModifierColumn.modifier_datetime)),
        getattr(model, spec.event_concept_id_column).label(
            str(ModifierColumn.modifier_concept_id)
        ),
        sa.literal(spec.event_source_table).label(
            str(ModifierColumn.modifier_source_table)
        ),
        # Guaranteed by ModifierSourceMixin, whatever the physical column name.
        model.modifier_of_event_id.label(str(ModifierColumn.target_event_id)),
        model.modifier_of_field_concept_id.label(
            str(ModifierColumn.target_field_concept_id)
        ),
    ]
    if include_values:
        columns.extend(
            _nullable_column(model, column, _VALUE_COLUMN_TYPES[column])
            for column in CANONICAL_MODIFIER_VALUE_COLUMNS
        )
    return sa.select(*columns)


def canonical_modifier_union(
    *models: type[Any], include_values: bool = True
) -> sa.Select[Any] | sa.CompoundSelect[Any]:
    projections = [
        canonical_modifier_projection(model, include_values=include_values)
        for model in models
    ]
    return _select_or_union_all(
        projections,
        error_message="canonical_modifier_union requires at least one model",
    )
