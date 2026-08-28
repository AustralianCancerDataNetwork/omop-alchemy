"""Portable temporal and repeated-observation SQL expressions."""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite

from omop_alchemy.toolkit.episodes.derivation import (
    ObservationSelectionPolicy,
    ObservationSelectionSpec,
    TemporalRankingSpec,
    TemporalSelectionPolicy,
    TemporalSidePreference,
    absolute_day_delta,
    bounded_temporal_predicate,
    episode_window_bounds,
    ranked_observation_select,
    signed_day_delta,
    temporal_order_expressions,
)


def _temporal_candidates() -> sa.CTE:
    return sa.union_all(
        sa.select(
            sa.literal(1001).label("episode_id"),
            sa.literal(date(2026, 1, 15)).label("start_date"),
        ),
        sa.select(
            sa.literal(1003).label("episode_id"),
            sa.literal(date(2026, 1, 21)).label("start_date"),
        ),
    ).cte("temporal_candidates")


@pytest.mark.parametrize("dialect", [sqlite.dialect(), postgresql.dialect()])
def test_day_delta_compiles_for_supported_dialects(dialect):
    statement = sa.select(
        signed_day_delta(sa.literal(date(2026, 1, 21)), sa.literal(date(2026, 1, 20))),
        absolute_day_delta(
            sa.literal(date(2026, 1, 15)),
            sa.literal(date(2026, 1, 20)),
        ),
    )

    compiled = str(statement.compile(dialect=dialect))
    assert "julianday" in compiled if dialect.name == "sqlite" else "CAST" in compiled


def test_day_delta_executes_as_signed_calendar_days(session):
    values = session.execute(
        sa.select(
            signed_day_delta(
                sa.literal(date(2026, 1, 21)),
                sa.literal(date(2026, 1, 20)),
            ),
            signed_day_delta(
                sa.literal(date(2026, 1, 15)),
                sa.literal(date(2026, 1, 20)),
            ),
        )
    ).one()

    assert tuple(values) == (1, -5)


def test_side_preference_is_applied_before_absolute_distance(session):
    candidates = _temporal_candidates()
    anchor = sa.literal(date(2026, 1, 20))
    neutral = TemporalRankingSpec(
        policy=TemporalSelectionPolicy.nearest,
        stable_id_column="episode_id",
    )
    started_first = TemporalRankingSpec(
        policy=TemporalSelectionPolicy.nearest,
        stable_id_column="episode_id",
        side_preference=TemporalSidePreference.on_or_before_anchor,
    )

    def first_id(spec: TemporalRankingSpec) -> int:
        statement = sa.select(candidates.c.episode_id).order_by(
            *temporal_order_expressions(
                candidates.c.start_date,
                anchor,
                candidates.c.episode_id,
                spec,
            )
        )
        value = session.scalar(statement.limit(1))
        assert value is not None
        return value

    assert first_id(neutral) == 1003
    assert first_id(started_first) == 1001


def test_episode_window_bounds_are_finite_and_boundary_policy_is_explicit(session):
    start = sa.literal(date(2026, 1, 15))
    end = sa.literal(None, type_=sa.Date())
    lower, upper = episode_window_bounds(
        start,
        end,
        days_prior=90,
        open_end_fallback_days=30,
    )
    values = session.execute(sa.select(lower, upper)).one()

    assert tuple(values) == (date(2025, 10, 17), date(2026, 2, 14))
    closed = bounded_temporal_predicate(
        sa.literal(date(2025, 10, 17)),
        lower,
        upper,
    )
    open_lower = bounded_temporal_predicate(
        sa.literal(date(2025, 10, 17)),
        lower,
        upper,
        include_lower_bound=False,
    )
    assert session.scalar(sa.select(closed)) is True
    assert session.scalar(sa.select(open_lower)) is False


def _observation_source() -> sa.CTE:
    rows = (
        (21, date(2026, 1, 1)),
        (22, date(2026, 1, 20)),
        (23, date(2026, 1, 20)),
        (24, date(2026, 1, 21)),
    )
    return sa.union_all(
        *(
            sa.select(
                sa.literal(101).label("person_id"),
                sa.literal(900_001).label("observation_concept_id"),
                sa.literal(observation_id).label("observation_id"),
                sa.literal(observation_date).label("observation_date"),
            )
            for observation_id, observation_date in rows
        )
    ).cte("observations")


def test_as_of_observation_selection_filters_before_ranking(session):
    source = _observation_source()
    spec = ObservationSelectionSpec(
        policy=ObservationSelectionPolicy.latest_on_or_before_anchor
    )
    ranked = ranked_observation_select(
        source,
        spec,
        anchor_date=sa.literal(date(2026, 1, 20)),
    ).subquery()
    selected = session.scalar(
        sa.select(ranked.c.observation_id).where(ranked.c.observation_rank == 1)
    )

    assert selected == 22


def test_as_of_observation_selection_can_exclude_the_anchor_date(session):
    source = _observation_source()
    spec = ObservationSelectionSpec(
        policy=ObservationSelectionPolicy.latest_on_or_before_anchor,
        include_anchor_date=False,
    )
    ranked = ranked_observation_select(
        source,
        spec,
        anchor_date=sa.literal(date(2026, 1, 20)),
    ).subquery()

    assert session.scalar(
        sa.select(ranked.c.observation_id).where(ranked.c.observation_rank == 1)
    ) == 21


def test_as_of_observation_selection_requires_an_anchor():
    with pytest.raises(ValueError, match="requires anchor_date"):
        ranked_observation_select(
            _observation_source(),
            ObservationSelectionSpec(
                policy=ObservationSelectionPolicy.latest_on_or_before_anchor
            ),
        )
