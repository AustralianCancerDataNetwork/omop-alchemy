"""Deterministic selection of one canonical modifier per target partition."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.sql.selectable import FromClause, SelectBase

from omop_alchemy.toolkit._utils import _as_from_clause, _require_columns
from omop_alchemy.toolkit.core._ranking import deterministic_row_number

from .contracts import ModifierColumn, ModifierSelectionPolicy, ModifierSelectionSpec

MODIFIER_RANK = "modifier_rank"


class InvalidModifierSourceError(ValueError):
    """Raised when a modifier selection input lacks a required column."""


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
    _require_columns(
        columns.keys(),
        required,
        role="modifier source",
        error_type=InvalidModifierSourceError,
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
    modifiers = _as_from_clause(source, name="modifier_selection_source")
    required = {
        *spec.partition_by,
        spec.date_column,
        spec.datetime_column,
        *spec.stable_identity_columns,
        str(ModifierColumn.target_event_id),
        str(ModifierColumn.target_field_concept_id),
    }
    _require_columns(
        modifiers.c.keys(),
        required,
        role="modifier source",
        error_type=InvalidModifierSourceError,
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
    """Select the first deterministically ranked modifier in each partition.

    Parameters
    ----------
    source:
        A selectable containing canonical modifier columns and target identity
        columns.
    spec:
        Temporal direction, partition columns and stable identity columns used
        to define the selection contract.
    priority:
        Optional SQL expressions placed before the temporal policy, such as a
        domain-specific stage preference.

    Returns
    -------
    sqlalchemy.sql.Select
        A selectable containing the source columns, with one row at rank one
        for each target partition. Rows missing either target identity column
        are excluded before selection.

    Notes
    -----
    The final tie-breakers come from ``spec.stable_identity_columns``. This
    keeps the result deterministic when dates, datetimes and caller priorities
    are equal.
    """
    ranked = ranked_modifier_select(source, spec=spec, priority=priority).subquery(
        "ranked_modifiers"
    )
    return sa.select(
        *(column for column in ranked.c if column.key != MODIFIER_RANK)
    ).where(ranked.c[MODIFIER_RANK] == 1)
