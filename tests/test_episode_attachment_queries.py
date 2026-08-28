"""Explicit-first event-to-episode attachment queries."""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite

from omop_alchemy.cdm.model import Procedure_Occurrence
from omop_alchemy.toolkit.core.events import ClinicalEventIdentity
from omop_alchemy.toolkit.episodes.derivation import (
    AttachmentDiagnosticCode,
    EpisodeAttachmentPolicy,
    TemporalRankingSpec,
    TemporalSelectionPolicy,
    TemporalSidePreference,
    episode_attachment_queries,
)
from tests.fixtures.query_contract_cases import (
    COLLIDING_EVENTS,
    CROSS_PERSON_LINK,
    DIRECTIONAL_PREFERENCE_EPISODES,
    OVERLAPPING_EPISODES,
    VALID_EXPLICIT_LINK,
    WRONG_DISCRIMINATOR_LINK,
    EpisodeCase,
    EventCase,
    ExplicitLinkCase,
    PROCEDURE_FIELD_CONCEPT_ID,
)


def _event_source(*events: EventCase) -> sa.CTE:
    return sa.union_all(
        *(
            sa.select(
                sa.literal(event.person_id).label("person_id"),
                sa.literal(event.identity.event_id).label("event_id"),
                sa.literal(event.event_date).label("event_date"),
                sa.cast(sa.null(), sa.DateTime()).label("event_datetime"),
                sa.literal(900_001).label("event_concept_id"),
                sa.literal(event.event_field_concept_id).label(
                    "event_field_concept_id"
                ),
                sa.literal(event.identity.event_source_table).label(
                    "event_source_table"
                ),
            )
            for event in events
        )
    ).cte("events")


def _episode_source(*episodes: EpisodeCase) -> sa.CTE:
    return sa.union_all(
        *(
            sa.select(
                sa.literal(episode.episode_id).label("episode_id"),
                sa.literal(episode.person_id).label("person_id"),
                sa.literal(episode.start_date).label("episode_start_date"),
                sa.literal(episode.end_date, type_=sa.Date()).label("episode_end_date"),
            )
            for episode in episodes
        )
    ).cte("episodes")


def _link_source(*links: ExplicitLinkCase) -> sa.CTE:
    return sa.union_all(
        *(
            sa.select(
                sa.literal(link.episode_id).label("episode_id"),
                sa.literal(link.event.event_id).label("event_id"),
                sa.literal(link.episode_event_field_concept_id).label(
                    "episode_event_field_concept_id"
                ),
            )
            for link in links
        )
    ).cte("episode_events")


def _empty_link_source() -> sa.CTE:
    return (
        sa.select(
            sa.cast(sa.null(), sa.Integer()).label("episode_id"),
            sa.cast(sa.null(), sa.Integer()).label("event_id"),
            sa.cast(sa.null(), sa.Integer()).label("episode_event_field_concept_id"),
        )
        .where(sa.false())
        .cte("episode_events")
    )


def _nearest(*, started_first: bool = False) -> TemporalRankingSpec:
    return TemporalRankingSpec(
        policy=TemporalSelectionPolicy.nearest,
        stable_id_column="episode_id",
        side_preference=(
            TemporalSidePreference.on_or_before_anchor
            if started_first
            else TemporalSidePreference.none
        ),
    )


def test_valid_explicit_links_suppress_ranked_fallback_with_colliding_ids(session):
    unlinked = EventCase(
        identity=ClinicalEventIdentity("procedure_occurrence", 8),
        person_id=101,
        event_date=date(2026, 1, 20),
        event_field_concept_id=PROCEDURE_FIELD_CONCEPT_ID,
    )
    sources = episode_attachment_queries(
        _event_source(*COLLIDING_EVENTS, unlinked),
        episodes=_episode_source(*OVERLAPPING_EPISODES),
        episode_events=_link_source(
            VALID_EXPLICIT_LINK,
            WRONG_DISCRIMINATOR_LINK,
            CROSS_PERSON_LINK,
        ),
        policy=EpisodeAttachmentPolicy.explicit_first_ranked,
        ranking=_nearest(),
    )

    rows = session.execute(sources.attachments).mappings().all()
    identities = {
        (row["event_source_table"], row["event_id"], row["episode_id"]) for row in rows
    }

    assert len(identities) == len(rows)
    assert ("measurement", 7, 1001) in identities
    assert ("procedure_occurrence", 7, 1002) in identities
    assert ("procedure_occurrence", 7, 1001) not in identities
    assert ("observation", 7, 2001) in identities
    assert ("procedure_occurrence", 8, 1001) in identities


def test_invalid_explicit_link_does_not_suppress_fallback(session):
    event = COLLIDING_EVENTS[2]
    sources = episode_attachment_queries(
        _event_source(event),
        episodes=_episode_source(*OVERLAPPING_EPISODES),
        episode_events=_link_source(CROSS_PERSON_LINK),
        policy=EpisodeAttachmentPolicy.explicit_first_ranked,
        ranking=_nearest(),
    )

    assert session.execute(sources.attachments).mappings().one()["episode_id"] == 2001


def test_explicit_only_returns_valid_links_without_fallback(session):
    queries = episode_attachment_queries(
        _event_source(*COLLIDING_EVENTS),
        episodes=_episode_source(*OVERLAPPING_EPISODES),
        episode_events=_link_source(
            VALID_EXPLICIT_LINK,
            WRONG_DISCRIMINATOR_LINK,
            CROSS_PERSON_LINK,
        ),
        policy=EpisodeAttachmentPolicy.explicit_only,
    )

    rows = session.execute(queries.attachments).mappings().all()

    assert queries.diagnostics is None
    assert {
        (row["event_source_table"], row["event_id"], row["episode_id"]) for row in rows
    } == {
        ("measurement", 7, 1001),
        ("procedure_occurrence", 7, 1002),
    }


def test_side_preference_is_applied_to_ranked_fallback(session):
    event = EventCase(
        identity=ClinicalEventIdentity("procedure_occurrence", 8),
        person_id=101,
        event_date=date(2026, 1, 20),
        event_field_concept_id=PROCEDURE_FIELD_CONCEPT_ID,
    )

    def selected_episode(ranking: TemporalRankingSpec) -> int:
        queries = episode_attachment_queries(
            _event_source(event),
            episodes=_episode_source(*DIRECTIONAL_PREFERENCE_EPISODES),
            episode_events=_empty_link_source(),
            policy=EpisodeAttachmentPolicy.explicit_first_ranked,
            ranking=ranking,
        )
        attachments = queries.attachments.subquery()
        value = session.scalar(sa.select(attachments.c.episode_id))
        assert value is not None
        return value

    assert selected_episode(_nearest()) == 1003
    assert selected_episode(_nearest(started_first=True)) == 1001


def test_all_in_window_fallback_retains_each_eligible_episode(session):
    event = EventCase(
        identity=ClinicalEventIdentity("procedure_occurrence", 8),
        person_id=101,
        event_date=date(2026, 1, 20),
        event_field_concept_id=PROCEDURE_FIELD_CONCEPT_ID,
    )
    queries = episode_attachment_queries(
        _event_source(event),
        episodes=_episode_source(*OVERLAPPING_EPISODES[:2]),
        episode_events=_empty_link_source(),
        policy=EpisodeAttachmentPolicy.explicit_first_all_in_window,
    )
    attachments = queries.attachments.subquery()

    assert set(session.scalars(sa.select(attachments.c.episode_id))) == {
        1001,
        1002,
    }


def test_diagnostics_explain_rejected_and_ambiguous_rows(session):
    ambiguous = EventCase(
        identity=ClinicalEventIdentity("procedure_occurrence", 8),
        person_id=101,
        event_date=date(2026, 1, 20),
        event_field_concept_id=PROCEDURE_FIELD_CONCEPT_ID,
    )
    unlinked = EventCase(
        identity=ClinicalEventIdentity("procedure_occurrence", 9),
        person_id=303,
        event_date=date(2026, 1, 20),
        event_field_concept_id=PROCEDURE_FIELD_CONCEPT_ID,
    )
    queries = episode_attachment_queries(
        _event_source(*COLLIDING_EVENTS, ambiguous, unlinked),
        episodes=_episode_source(*OVERLAPPING_EPISODES),
        episode_events=_link_source(
            VALID_EXPLICIT_LINK,
            WRONG_DISCRIMINATOR_LINK,
            CROSS_PERSON_LINK,
        ),
        policy=EpisodeAttachmentPolicy.explicit_first_ranked,
        ranking=_nearest(),
        include_diagnostics=True,
    )
    assert queries.diagnostics is not None

    rows = session.execute(queries.diagnostics).mappings().all()
    codes = {row["diagnostic_code"] for row in rows}
    ambiguous_rows = [
        row
        for row in rows
        if row["diagnostic_code"] == str(AttachmentDiagnosticCode.ambiguous_fallback)
        and row["event_id"] == 8
    ]

    assert str(AttachmentDiagnosticCode.discriminator_mismatch) in codes
    assert str(AttachmentDiagnosticCode.person_mismatch) in codes
    assert str(AttachmentDiagnosticCode.no_candidate_episode) in codes
    assert len(ambiguous_rows) == 1
    assert ambiguous_rows[0]["candidate_count"] == 2


def test_ranked_policy_requires_a_ranking_contract():
    with pytest.raises(ValueError, match="requires a temporal ranking"):
        episode_attachment_queries(
            _event_source(COLLIDING_EVENTS[0]),
            episodes=_episode_source(OVERLAPPING_EPISODES[0]),
            episode_events=_link_source(WRONG_DISCRIMINATOR_LINK),
            policy=EpisodeAttachmentPolicy.explicit_first_ranked,
        )


@pytest.mark.parametrize("dialect", [sqlite.dialect(), postgresql.dialect()])
def test_attachment_and_diagnostics_compile_on_supported_dialects(dialect):
    queries = episode_attachment_queries(
        _event_source(*COLLIDING_EVENTS),
        episodes=_episode_source(*OVERLAPPING_EPISODES),
        episode_events=_link_source(
            VALID_EXPLICIT_LINK,
            WRONG_DISCRIMINATOR_LINK,
            CROSS_PERSON_LINK,
        ),
        policy=EpisodeAttachmentPolicy.explicit_first_ranked,
        ranking=_nearest(),
        include_diagnostics=True,
    )

    str(queries.attachments.compile(dialect=dialect))
    assert queries.diagnostics is not None
    str(queries.diagnostics.compile(dialect=dialect))


def test_attachment_builder_accepts_a_supported_event_model():
    queries = episode_attachment_queries(
        Procedure_Occurrence,
        policy=EpisodeAttachmentPolicy.explicit_only,
    )

    assert "procedure_occurrence" in str(
        queries.attachments.compile(dialect=postgresql.dialect())
    )
