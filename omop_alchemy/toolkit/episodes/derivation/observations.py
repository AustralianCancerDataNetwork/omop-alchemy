"""Reusable SQL selection for repeated longitudinal observations."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from ._ranking import deterministic_row_number
from .contracts import ObservationSelectionPolicy, ObservationSelectionSpec


def observation_eligibility_predicate(
    observation_date: sa.ColumnElement[Any],
    spec: ObservationSelectionSpec,
    *,
    anchor_date: sa.ColumnElement[Any] | None = None,
) -> sa.ColumnElement[bool]:
    """Return the date predicate required by an observation selection policy."""
    if not spec.requires_anchor:
        return sa.true()
    if anchor_date is None:
        raise ValueError(f"{spec.policy} requires anchor_date")
    return (
        observation_date <= anchor_date
        if spec.include_anchor_date
        else observation_date < anchor_date
    )


def observation_order_expressions(
    observation_date: sa.ColumnElement[Any],
    stable_id: sa.ColumnElement[Any],
    spec: ObservationSelectionSpec,
) -> tuple[sa.ColumnElement[Any], ...]:
    """Build date and stable-ID ordering for repeated observations."""
    if spec.policy is ObservationSelectionPolicy.earliest:
        date_order = observation_date.asc()
    elif spec.policy in (
        ObservationSelectionPolicy.latest,
        ObservationSelectionPolicy.latest_on_or_before_anchor,
    ):
        date_order = observation_date.desc()
    else:  # pragma: no cover - StrEnum construction prevents unknown policies
        raise ValueError(f"Unsupported observation selection policy: {spec.policy}")
    return date_order, stable_id.asc()


def observation_row_number(
    columns: Any,
    *,
    observation_date_column: str,
    spec: ObservationSelectionSpec,
    label: str = "observation_rank",
) -> sa.ColumnElement[int]:
    """Return a deterministic row number using the declared observation grain."""
    try:
        observation_date = columns[observation_date_column]
        stable_id = columns[spec.stable_id_column]
        partition_by = tuple(columns[name] for name in spec.partition_by)
    except KeyError as error:
        raise ValueError(
            f"Observation selection column is missing: {error.args[0]}"
        ) from error
    return deterministic_row_number(
        partition_by=partition_by,
        order_by=observation_order_expressions(observation_date, stable_id, spec),
        label=label,
    )


def ranked_observation_select(
    source: sa.FromClause,
    spec: ObservationSelectionSpec,
    *,
    observation_date_column: str = "observation_date",
    anchor_date: sa.ColumnElement[Any] | None = None,
    rank_label: str = "observation_rank",
) -> sa.Select[Any]:
    """Select source columns with deterministic observation rank and eligibility."""
    columns = source.c
    try:
        observation_date = columns[observation_date_column]
    except KeyError as error:
        raise ValueError(
            f"Observation selection column is missing: {observation_date_column}"
        ) from error

    rank = observation_row_number(
        columns,
        observation_date_column=observation_date_column,
        spec=spec,
        label=rank_label,
    )
    return sa.select(*columns, rank).where(
        observation_eligibility_predicate(
            observation_date,
            spec,
            anchor_date=anchor_date,
        )
    )
