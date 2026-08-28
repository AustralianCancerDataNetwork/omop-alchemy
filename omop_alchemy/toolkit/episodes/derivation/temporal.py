"""Portable SQL expressions for bounded windows and deterministic date ranking."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.functions import FunctionElement

from omop_alchemy.toolkit.episodes.handling.event_windowing import (
    DEFAULT_EPISODE_OPEN_END_FALLBACK_DAYS,
    DEFAULT_EPISODE_WINDOW_DAYS_PRIOR,
)

from .contracts import (
    TemporalRankingSpec,
    TemporalSelectionPolicy,
    TemporalSidePreference,
)


class _SignedDayDelta(FunctionElement[int]):
    type = sa.Integer()
    inherit_cache = True


@compiles(_SignedDayDelta)
@compiles(_SignedDayDelta, "postgresql")
def _compile_signed_day_delta(
    element: _SignedDayDelta,
    compiler: SQLCompiler,
    **kwargs: Any,
) -> str:
    candidate, anchor = list(element.clauses)
    return (
        f"(CAST({compiler.process(candidate, **kwargs)} AS DATE) - "
        f"CAST({compiler.process(anchor, **kwargs)} AS DATE))"
    )


@compiles(_SignedDayDelta, "sqlite")
def _compile_sqlite_signed_day_delta(
    element: _SignedDayDelta,
    compiler: SQLCompiler,
    **kwargs: Any,
) -> str:
    candidate, anchor = list(element.clauses)
    return (
        "CAST((julianday(date("
        f"{compiler.process(candidate, **kwargs)})) - julianday(date("
        f"{compiler.process(anchor, **kwargs)}))) AS INTEGER)"
    )


class _ShiftDate(FunctionElement[Any]):
    type = sa.Date()
    inherit_cache = True


@compiles(_ShiftDate)
@compiles(_ShiftDate, "postgresql")
def _compile_shift_date(
    element: _ShiftDate,
    compiler: SQLCompiler,
    **kwargs: Any,
) -> str:
    value, days = list(element.clauses)
    return (
        f"(CAST({compiler.process(value, **kwargs)} AS DATE) + "
        f"CAST({compiler.process(days, **kwargs)} AS INTEGER))"
    )


@compiles(_ShiftDate, "sqlite")
def _compile_sqlite_shift_date(
    element: _ShiftDate,
    compiler: SQLCompiler,
    **kwargs: Any,
) -> str:
    value, days = list(element.clauses)
    return (
        f"date({compiler.process(value, **kwargs)}, "
        f"printf('%+d days', {compiler.process(days, **kwargs)}))"
    )


def signed_day_delta(
    candidate_date: sa.ColumnElement[Any],
    anchor_date: sa.ColumnElement[Any],
) -> sa.ColumnElement[int]:
    """Return candidate minus anchor in whole calendar days."""
    return _SignedDayDelta(candidate_date, anchor_date)


def absolute_day_delta(
    candidate_date: sa.ColumnElement[Any],
    anchor_date: sa.ColumnElement[Any],
) -> sa.ColumnElement[int]:
    """Return absolute calendar-day distance between candidate and anchor."""
    return sa.func.abs(signed_day_delta(candidate_date, anchor_date))


def shift_date(
    value: sa.ColumnElement[Any],
    *,
    days: int,
) -> sa.ColumnElement[Any]:
    """Shift a date by a fixed number of days on PostgreSQL or SQLite."""
    return _ShiftDate(value, sa.literal(days))


def bounded_temporal_predicate(
    value: sa.ColumnElement[Any],
    lower_bound: sa.ColumnElement[Any],
    upper_bound: sa.ColumnElement[Any],
    *,
    include_lower_bound: bool = True,
    include_upper_bound: bool = True,
) -> sa.ColumnElement[bool]:
    """Test a value against independently open or closed temporal bounds."""
    lower = value >= lower_bound if include_lower_bound else value > lower_bound
    upper = value <= upper_bound if include_upper_bound else value < upper_bound
    return sa.and_(lower, upper)


def episode_window_bounds(
    episode_start_date: sa.ColumnElement[Any],
    episode_end_date: sa.ColumnElement[Any],
    *,
    days_prior: int = DEFAULT_EPISODE_WINDOW_DAYS_PRIOR,
    open_end_fallback_days: int = DEFAULT_EPISODE_OPEN_END_FALLBACK_DAYS,
) -> tuple[sa.ColumnElement[Any], sa.ColumnElement[Any]]:
    """Build the bounded SQL interval used for date-admitted episode facts."""
    if days_prior < 0:
        raise ValueError("days_prior must be non-negative")
    if open_end_fallback_days < 0:
        raise ValueError("open_end_fallback_days must be non-negative")
    lower = shift_date(episode_start_date, days=-days_prior)
    upper = sa.func.coalesce(
        episode_end_date,
        shift_date(episode_start_date, days=open_end_fallback_days),
    )
    return lower, upper


def episode_window_predicate(
    event_date: sa.ColumnElement[Any],
    episode_start_date: sa.ColumnElement[Any],
    episode_end_date: sa.ColumnElement[Any],
    *,
    ranking: TemporalRankingSpec | None = None,
    days_prior: int = DEFAULT_EPISODE_WINDOW_DAYS_PRIOR,
    open_end_fallback_days: int = DEFAULT_EPISODE_OPEN_END_FALLBACK_DAYS,
) -> sa.ColumnElement[bool]:
    """Test whether an event lies inside a bounded episode-relative window."""
    lower, upper = episode_window_bounds(
        episode_start_date,
        episode_end_date,
        days_prior=days_prior,
        open_end_fallback_days=open_end_fallback_days,
    )
    return bounded_temporal_predicate(
        event_date,
        lower,
        upper,
        include_lower_bound=ranking.include_lower_bound if ranking else True,
        include_upper_bound=ranking.include_upper_bound if ranking else True,
    )


def temporal_order_expressions(
    candidate_date: sa.ColumnElement[Any],
    anchor_date: sa.ColumnElement[Any],
    stable_id: sa.ColumnElement[Any],
    ranking: TemporalRankingSpec,
) -> tuple[sa.ColumnElement[Any], ...]:
    """Build deterministic ordering for a temporal ranking contract."""
    order: list[sa.ColumnElement[Any]] = []
    if ranking.side_preference is TemporalSidePreference.on_or_before_anchor:
        order.append(sa.case((candidate_date <= anchor_date, 0), else_=1).asc())
    elif ranking.side_preference is TemporalSidePreference.on_or_after_anchor:
        order.append(sa.case((candidate_date >= anchor_date, 0), else_=1).asc())

    if ranking.policy is TemporalSelectionPolicy.nearest:
        order.append(absolute_day_delta(candidate_date, anchor_date).asc())
    elif ranking.policy is TemporalSelectionPolicy.earliest:
        order.append(candidate_date.asc())
    elif ranking.policy is TemporalSelectionPolicy.latest:
        order.append(candidate_date.desc())
    else:  # pragma: no cover - StrEnum construction prevents unknown policies
        raise ValueError(f"Unsupported temporal selection policy: {ranking.policy}")
    order.append(stable_id.asc())
    return tuple(order)


def temporal_row_number(
    candidate_date: sa.ColumnElement[Any],
    anchor_date: sa.ColumnElement[Any],
    stable_id: sa.ColumnElement[Any],
    ranking: TemporalRankingSpec,
    *,
    partition_by: Iterable[sa.ColumnElement[Any]] = (),
    label: str = "temporal_rank",
) -> sa.ColumnElement[int]:
    """Return a deterministic row number for temporal candidates."""
    return sa.func.row_number().over(
        partition_by=tuple(partition_by),
        order_by=temporal_order_expressions(
            candidate_date,
            anchor_date,
            stable_id,
            ranking,
        ),
    ).label(label)
