"""Deterministic selection of one canonical modifier per target partition."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.sql.selectable import FromClause, SelectBase

from omop_alchemy.toolkit.core._ranking import deterministic_row_number

from .contracts import ModifierColumn, ModifierSelectionPolicy, ModifierSelectionSpec

MODIFIER_RANK = "modifier_rank"


class InvalidModifierSourceError(ValueError):
    pass


def _as_source(source: FromClause | SelectBase, name: str) -> FromClause:
    if isinstance(source, SelectBase):
        return source.subquery(name)
    if isinstance(source, FromClause):
        return source
    raise TypeError("source must be a SQLAlchemy Select or FromClause")


def modifier_order_expressions(
    columns: sa.sql.base.ReadOnlyColumnCollection[str, Any],
    spec: ModifierSelectionSpec,
    *,
    priority: Sequence[sa.ColumnElement[Any]] = (),
) -> tuple[sa.ColumnElement[Any], ...]:
    """Return the complete, portable order for a modifier selection policy."""
    required = {
        *spec.partition_by,
        spec.date_column,
        spec.datetime_column,
        *spec.stable_identity_columns,
    }
    missing = tuple(sorted(required.difference(columns.keys())))
    if missing:
        raise InvalidModifierSourceError(
            f"modifier source is missing required columns: {', '.join(missing)}"
        )
    direction = sa.desc if spec.policy is ModifierSelectionPolicy.latest else sa.asc
    date_column = columns[spec.date_column]
    datetime_column = columns[spec.datetime_column]
    return (
        *priority,
        date_column.is_(None).asc(),
        direction(date_column),
        datetime_column.is_(None).asc(),
        direction(datetime_column),
        *(columns[name].asc() for name in spec.stable_identity_columns),
    )


def modifier_row_number(
    columns: sa.sql.base.ReadOnlyColumnCollection[str, Any],
    spec: ModifierSelectionSpec,
    *,
    priority: Sequence[sa.ColumnElement[Any]] = (),
    label: str = MODIFIER_RANK,
) -> sa.ColumnElement[int]:
    """Build the deterministic window rank for canonical modifier columns."""
    order_by = modifier_order_expressions(columns, spec, priority=priority)
    return deterministic_row_number(
        partition_by=(columns[name] for name in spec.partition_by),
        order_by=order_by,
        label=label,
    )


def ranked_modifier_select(
    source: FromClause | SelectBase,
    spec: ModifierSelectionSpec = ModifierSelectionSpec(),
    *,
    priority: Sequence[sa.ColumnElement[Any]] = (),
    rank_label: str = MODIFIER_RANK,
) -> sa.Select[Any]:
    """Rank bound modifiers, with caller priorities preceding temporal policy."""
    modifiers = _as_source(source, "modifier_selection_source")
    required = {
        *spec.partition_by,
        spec.date_column,
        spec.datetime_column,
        *spec.stable_identity_columns,
        str(ModifierColumn.target_event_id),
        str(ModifierColumn.target_field_concept_id),
    }
    missing = tuple(sorted(required.difference(modifiers.c.keys())))
    if missing:
        raise InvalidModifierSourceError(
            f"modifier source is missing required columns: {', '.join(missing)}"
        )

    rank = modifier_row_number(
        modifiers.c,
        spec,
        priority=priority,
        label=rank_label,
    )
    return sa.select(*modifiers.c, rank).where(
        modifiers.c[str(ModifierColumn.target_event_id)].is_not(None),
        modifiers.c[str(ModifierColumn.target_field_concept_id)].is_not(None),
    )


def selected_modifier_select(
    source: FromClause | SelectBase,
    spec: ModifierSelectionSpec = ModifierSelectionSpec(),
    *,
    priority: Sequence[sa.ColumnElement[Any]] = (),
) -> sa.Select[Any]:
    """Select the first deterministically ranked modifier in each partition."""
    ranked = ranked_modifier_select(source, spec=spec, priority=priority).subquery(
        "ranked_modifiers"
    )
    return sa.select(
        *(column for column in ranked.c if column.key != MODIFIER_RANK)
    ).where(ranked.c[MODIFIER_RANK] == 1)
