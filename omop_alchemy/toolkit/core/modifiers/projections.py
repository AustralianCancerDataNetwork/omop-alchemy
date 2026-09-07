"""Canonical SQLAlchemy projections for OMOP modifier-bearing tables."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from .contracts import ModifierColumn
from .metadata import modifier_source_model_spec


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
        getattr(model, spec.modifier_id_attribute).label(
            str(ModifierColumn.modifier_id)
        ),
        getattr(model, spec.modifier_date_attribute).label(
            str(ModifierColumn.modifier_date)
        ),
        getattr(model, spec.modifier_datetime_attribute).label(
            str(ModifierColumn.modifier_datetime)
        ),
        getattr(model, spec.modifier_concept_id_attribute).label(
            str(ModifierColumn.modifier_concept_id)
        ),
        sa.literal(spec.modifier_source_table).label(
            str(ModifierColumn.modifier_source_table)
        ),
        getattr(model, spec.target_event_id_attribute).label(
            str(ModifierColumn.target_event_id)
        ),
        getattr(model, spec.target_field_concept_id_attribute).label(
            str(ModifierColumn.target_field_concept_id)
        ),
    ]
    if include_values:
        columns.extend(
            (
                _nullable(model, ModifierColumn.value_as_number, sa.Float()),
                _nullable(model, ModifierColumn.value_as_concept_id, sa.Integer()),
                _nullable(model, ModifierColumn.unit_concept_id, sa.Integer()),
                _nullable(model, ModifierColumn.value_as_string, sa.String()),
            )
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
