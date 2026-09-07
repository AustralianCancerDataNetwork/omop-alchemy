"""Canonical SQLAlchemy projections for OMOP modifier-bearing tables."""

from __future__ import annotations

from typing import Any, Mapping

import sqlalchemy as sa

from .contracts import CANONICAL_MODIFIER_VALUE_COLUMNS, ModifierColumn
from .metadata import modifier_source_model_spec


# The SQL type each value position is cast to when a source has no such column.
# Keyed by column so the emission order below comes from the contract rather
# than from a second hand-maintained list.
_VALUE_COLUMN_TYPES: Mapping[ModifierColumn, sa.types.TypeEngine[Any]] = {
    ModifierColumn.value_as_number: sa.Float(),
    ModifierColumn.value_as_concept_id: sa.Integer(),
    ModifierColumn.unit_concept_id: sa.Integer(),
    ModifierColumn.value_as_string: sa.String(),
}


def _nullable(
    model: type[Any], name: ModifierColumn, sql_type: sa.types.TypeEngine[Any]
) -> sa.ColumnElement[Any]:
    column = getattr(model, str(name), None)
    if column is None:
        return sa.cast(sa.null(), sql_type).label(str(name))
    return column.label(str(name))


def canonical_modifier_projection(
    model: type[Any], *, include_values: bool = True
) -> sa.Select[Any]:
    """Project Measurement or Observation into one modifier row shape."""
    spec = modifier_source_model_spec(model)
    columns: list[sa.ColumnElement[Any]] = [
        model.person_id.label(str(ModifierColumn.person_id)),
        getattr(model, spec.event_id_column).label(
            str(ModifierColumn.modifier_id)
        ),
        getattr(model, spec.event_date_column).label(
            str(ModifierColumn.modifier_date)
        ),
        getattr(model, spec.event_datetime_column).label(
            str(ModifierColumn.modifier_datetime)
        ),
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
            _nullable(model, column, _VALUE_COLUMN_TYPES[column])
            for column in CANONICAL_MODIFIER_VALUE_COLUMNS
        )
    return sa.select(*columns)


def canonical_modifier_union(
    *models: type[Any], include_values: bool = True
) -> sa.Select[Any] | sa.CompoundSelect[Any]:
    if not models:
        raise ValueError("canonical_modifier_union requires at least one model")
    projections = [
        canonical_modifier_projection(model, include_values=include_values)
        for model in models
    ]
    return projections[0] if len(projections) == 1 else sa.union_all(*projections)
