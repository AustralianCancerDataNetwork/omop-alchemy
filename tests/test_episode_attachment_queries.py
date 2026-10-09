"""Explicit-first event-to-episode attachment queries."""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite

from omop_alchemy.cdm.base import ModifierFieldConcepts
from omop_alchemy.cdm.model import Procedure_Occurrence
from omop_alchemy.cdm.model.structural import Episode
from omop_alchemy.toolkit.core.events import ClinicalEventIdentity
from omop_alchemy.toolkit.episodes.derivation import (
    AttachmentDiagnosticCode,
    EpisodeAttachmentDiagnostic,
    EpisodeAttachmentPolicy,
    EpisodeWindowSpec,
    TemporalRankingSpec,
    TemporalSelectionPolicy,
    TemporalSidePreference,
    episode_attachment_queries,
)
from omop_alchemy.toolkit.episodes.derivation.attachments import (
    InvalidAttachmentSourceError,
)
from tests.fixtures.query_contract_cases import (
    COLLIDING_EVENTS,
    CROSS_PERSON_LINK,
    DIRECTIONAL_PREFERENCE_EPISODES,
    OVERLAPPING_EPISODES,
    OUT_OF_SCOPE_LINK,
    VALID_EXPLICIT_LINK,
    COLLIDING_VALID_LINK,
    EpisodeCase,
    EventCase,
    ExplicitLinkCase,
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


def _episode_source(*episodes: EpisodeCase, name: str = "episodes") -> sa.CTE:
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
    ).cte(name)


def _shared_episodes(source: sa.CTE) -> dict[str, sa.CTE]:
    """Use one episode population for explicit validation and fallback."""
    return {"explicit_episodes": source, "fallback_episodes": source}


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
        event_field_concept_id=ModifierFieldConcepts.PROCEDURE_OCCURRENCE,
    )
    sources = episode_attachment_queries(
        _event_source(*COLLIDING_EVENTS, unlinked),
        **_shared_episodes(_episode_source(*OVERLAPPING_EPISODES)),
        episode_events=_link_source(
            VALID_EXPLICIT_LINK,
            COLLIDING_VALID_LINK,
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
        **_shared_episodes(_episode_source(*OVERLAPPING_EPISODES)),
        episode_events=_link_source(CROSS_PERSON_LINK),
        policy=EpisodeAttachmentPolicy.explicit_first_ranked,
        ranking=_nearest(),
    )

    assert session.execute(sources.attachments).mappings().one()["episode_id"] == 2001


def test_foreign_discriminator_link_is_out_of_scope_for_a_single_model(session):
    queries = episode_attachment_queries(
        _event_source(COLLIDING_EVENTS[0]),
        explicit_episodes=_episode_source(*OVERLAPPING_EPISODES[:2]),
        episode_events=_link_source(COLLIDING_VALID_LINK, VALID_EXPLICIT_LINK),
        policy=EpisodeAttachmentPolicy.explicit_only,
        include_diagnostics=True,
    )

    assert session.execute(queries.attachments).mappings().one()["episode_id"] == 1001
    assert queries.diagnostics is not None
    assert session.execute(queries.diagnostics).all() == []


def test_explicit_only_returns_valid_links_without_fallback(session):
    queries = episode_attachment_queries(
        _event_source(*COLLIDING_EVENTS),
        explicit_episodes=_episode_source(*OVERLAPPING_EPISODES),
        episode_events=_link_source(
            VALID_EXPLICIT_LINK,
            COLLIDING_VALID_LINK,
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
        event_field_concept_id=ModifierFieldConcepts.PROCEDURE_OCCURRENCE,
    )

    def selected_episode(ranking: TemporalRankingSpec) -> int:
        queries = episode_attachment_queries(
            _event_source(event),
            **_shared_episodes(_episode_source(*DIRECTIONAL_PREFERENCE_EPISODES)),
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
        event_field_concept_id=ModifierFieldConcepts.PROCEDURE_OCCURRENCE,
    )
    queries = episode_attachment_queries(
        _event_source(event),
        **_shared_episodes(_episode_source(*OVERLAPPING_EPISODES[:2])),
        episode_events=_empty_link_source(),
        policy=EpisodeAttachmentPolicy.explicit_first_all_in_window,
    )
    attachments = queries.attachments.subquery()

    assert set(session.scalars(sa.select(attachments.c.episode_id))) == {
        1001,
        1002,
    }


def test_all_in_window_uses_a_window_contract_without_ranking(session):
    boundary_event = EventCase(
        identity=ClinicalEventIdentity("procedure_occurrence", 8),
        person_id=101,
        event_date=date(2025, 10, 17),
        event_field_concept_id=ModifierFieldConcepts.PROCEDURE_OCCURRENCE,
    )
    queries = episode_attachment_queries(
        _event_source(boundary_event),
        **_shared_episodes(_episode_source(OVERLAPPING_EPISODES[0])),
        episode_events=_empty_link_source(),
        policy=EpisodeAttachmentPolicy.explicit_first_all_in_window,
        window=EpisodeWindowSpec(include_lower_bound=False),
    )

    assert session.execute(queries.attachments).all() == []


def test_all_in_window_diagnostics_do_not_report_intended_fanout(session):
    event = EventCase(
        identity=ClinicalEventIdentity("procedure_occurrence", 8),
        person_id=101,
        event_date=date(2026, 1, 20),
        event_field_concept_id=ModifierFieldConcepts.PROCEDURE_OCCURRENCE,
    )
    queries = episode_attachment_queries(
        _event_source(event),
        **_shared_episodes(_episode_source(*OVERLAPPING_EPISODES[:2])),
        episode_events=_empty_link_source(),
        policy=EpisodeAttachmentPolicy.explicit_first_all_in_window,
        include_diagnostics=True,
    )

    assert queries.diagnostics is not None
    assert session.execute(queries.diagnostics).mappings().all() == []


@pytest.mark.parametrize(
    "policy",
    [
        EpisodeAttachmentPolicy.explicit_only,
        EpisodeAttachmentPolicy.explicit_first_all_in_window,
    ],
)
def test_non_ranked_attachment_policies_reject_ranking(policy):
    with pytest.raises(ValueError, match="does not use a temporal ranking"):
        episode_attachment_queries(
            _event_source(COLLIDING_EVENTS[0]),
            explicit_episodes=_episode_source(OVERLAPPING_EPISODES[0]),
            episode_events=_empty_link_source(),
            policy=policy,
            ranking=_nearest(),
        )


def test_diagnostics_explain_person_mismatches_and_fallback_outcomes(session):
    ambiguous = EventCase(
        identity=ClinicalEventIdentity("procedure_occurrence", 8),
        person_id=101,
        event_date=date(2026, 1, 20),
        event_field_concept_id=ModifierFieldConcepts.PROCEDURE_OCCURRENCE,
    )
    unlinked = EventCase(
        identity=ClinicalEventIdentity("procedure_occurrence", 9),
        person_id=303,
        event_date=date(2026, 1, 20),
        event_field_concept_id=ModifierFieldConcepts.PROCEDURE_OCCURRENCE,
    )
    queries = episode_attachment_queries(
        _event_source(*COLLIDING_EVENTS, ambiguous, unlinked),
        **_shared_episodes(_episode_source(*OVERLAPPING_EPISODES)),
        episode_events=_link_source(
            VALID_EXPLICIT_LINK,
            COLLIDING_VALID_LINK,
            OUT_OF_SCOPE_LINK,
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

    assert str(AttachmentDiagnosticCode.person_mismatch) in codes
    assert str(AttachmentDiagnosticCode.no_candidate_episode) in codes
    assert len(ambiguous_rows) == 1
    assert ambiguous_rows[0]["candidate_count"] == 2
    person_rows = [
        row
        for row in rows
        if row["diagnostic_code"] == str(AttachmentDiagnosticCode.person_mismatch)
    ]
    typed = EpisodeAttachmentDiagnostic.from_mapping(person_rows[0])
    assert typed.code is AttachmentDiagnosticCode.person_mismatch
    assert typed.event.event_id == 7
    assert (
        typed.linked_event_field_concept_id
        == CROSS_PERSON_LINK.episode_event_field_concept_id
    )
    assert typed.episode_id == CROSS_PERSON_LINK.episode_id


@pytest.mark.parametrize(
    "session_fixture",
    [
        "session",
        pytest.param("pg_session", marks=[pytest.mark.postgresql, pytest.mark.db_dialect]),
    ],
)
@pytest.mark.parametrize("copies", [(2, 1), (1, 2), (2, 2)])
@pytest.mark.parametrize("episode_count", [1, 2])
@pytest.mark.parametrize(
    "policy",
    [
        EpisodeAttachmentPolicy.explicit_first_ranked,
        EpisodeAttachmentPolicy.explicit_first_all_in_window,
    ],
)
def test_fallback_counts_distinct_episodes_with_duplicate_inputs(
    request, session_fixture, copies, episode_count, policy
):
    session = request.getfixturevalue(session_fixture)
    event_copies, episode_copies = copies
    queries = episode_attachment_queries(
        _event_source(*([COLLIDING_EVENTS[0]] * event_copies)),
        **_shared_episodes(
            _episode_source(*(OVERLAPPING_EPISODES[:episode_count] * episode_copies))
        ),
        episode_events=_empty_link_source(),
        policy=policy,
        ranking=_nearest() if policy.requires_fallback_ranking else None,
        include_diagnostics=True,
    )
    rows = session.execute(queries.attachments).mappings().all()
    expected_ids = (
        {1001}
        if policy.requires_fallback_ranking
        else {episode.episode_id for episode in OVERLAPPING_EPISODES[:episode_count]}
    )
    assert len(rows) == len(expected_ids)
    assert {row["episode_id"] for row in rows} == expected_ids
    assert queries.diagnostics is not None
    diagnostics = session.execute(queries.diagnostics).mappings().all()
    if episode_count == 2 and policy.requires_fallback_ranking:
        assert len(diagnostics) == 1
        assert diagnostics[0]["diagnostic_code"] == "ambiguous_fallback"
        assert diagnostics[0]["candidate_count"] == 2
    else:
        assert diagnostics == []


def test_diagnostics_and_fallback_share_the_explicit_event_key_cte():
    queries = episode_attachment_queries(
        _event_source(COLLIDING_EVENTS[0]),
        **_shared_episodes(_episode_source(*OVERLAPPING_EPISODES)),
        episode_events=_empty_link_source(),
        policy=EpisodeAttachmentPolicy.explicit_first_ranked,
        ranking=_nearest(),
        include_diagnostics=True,
    )

    assert queries.diagnostics is not None
    compiled = str(queries.diagnostics.compile(dialect=postgresql.dialect()))

    assert "valid_explicit_event_keys_for_fallback" not in compiled
    assert compiled.count("valid_explicit_event_keys AS") == 1


def test_ranked_policy_requires_a_ranking_contract():
    with pytest.raises(ValueError, match="requires a temporal ranking"):
        episode_attachment_queries(
            _event_source(COLLIDING_EVENTS[0]),
            explicit_episodes=_episode_source(OVERLAPPING_EPISODES[0]),
            episode_events=_link_source(OUT_OF_SCOPE_LINK),
            policy=EpisodeAttachmentPolicy.explicit_first_ranked,
        )


@pytest.mark.parametrize("dialect", [sqlite.dialect(), postgresql.dialect()])
def test_attachment_and_diagnostics_compile_on_supported_dialects(dialect):
    queries = episode_attachment_queries(
        _event_source(*COLLIDING_EVENTS),
        **_shared_episodes(_episode_source(*OVERLAPPING_EPISODES)),
        episode_events=_link_source(
            VALID_EXPLICIT_LINK,
            COLLIDING_VALID_LINK,
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


def test_episode_model_can_supply_both_stages():
    queries = episode_attachment_queries(
        Procedure_Occurrence,
        explicit_episodes=Episode,
        fallback_episodes=Episode,
        policy=EpisodeAttachmentPolicy.explicit_first_ranked,
        ranking=_nearest(),
    )

    compiled = str(queries.attachments.compile(dialect=postgresql.dialect()))
    assert "attachment_explicit_episodes" in compiled
    assert "attachment_fallback_episodes" in compiled


@pytest.mark.db_dialect
def test_postgresql_executes_collision_and_stable_tie_contracts(pg_session):
    unlinked = EventCase(
        identity=ClinicalEventIdentity("procedure_occurrence", 8),
        person_id=101,
        event_date=date(2026, 1, 20),
        event_field_concept_id=ModifierFieldConcepts.PROCEDURE_OCCURRENCE,
    )
    queries = episode_attachment_queries(
        _event_source(*COLLIDING_EVENTS[:2], unlinked),
        **_shared_episodes(_episode_source(*OVERLAPPING_EPISODES[:2])),
        episode_events=_link_source(VALID_EXPLICIT_LINK, COLLIDING_VALID_LINK),
        policy=EpisodeAttachmentPolicy.explicit_first_ranked,
        ranking=_nearest(),
        include_diagnostics=True,
    )

    rows = pg_session.execute(queries.attachments).mappings().all()
    assert {
        (row["event_source_table"], row["event_id"], row["episode_id"]) for row in rows
    } == {
        ("measurement", 7, 1001),
        ("procedure_occurrence", 7, 1002),
        ("procedure_occurrence", 8, 1001),
    }

    single_model = episode_attachment_queries(
        _event_source(COLLIDING_EVENTS[0]),
        explicit_episodes=_episode_source(*OVERLAPPING_EPISODES[:2]),
        episode_events=_link_source(COLLIDING_VALID_LINK, VALID_EXPLICIT_LINK),
        policy=EpisodeAttachmentPolicy.explicit_only,
        include_diagnostics=True,
    )
    assert (
        pg_session.execute(single_model.attachments).mappings().one()["episode_id"]
        == 1001
    )
    assert single_model.diagnostics is not None
    assert pg_session.execute(single_model.diagnostics).all() == []


# A disease root and a nested child that starts later, as built for a top-level
# diagnosis and a diagnosis recorded beneath it. The builder does not know which
# is which; the caller expresses that by choosing each stage's source.
ROOT_EPISODE = EpisodeCase(
    3001, person_id=101, start_date=date(2025, 1, 10), end_date=date(2025, 12, 31)
)
CHILD_EPISODE = EpisodeCase(
    3002, person_id=101, start_date=date(2025, 6, 1), end_date=date(2025, 12, 31)
)


def _procedure(event_id: int, event_date: date) -> EventCase:
    return EventCase(
        identity=ClinicalEventIdentity("procedure_occurrence", event_id),
        person_id=101,
        event_date=event_date,
        event_field_concept_id=ModifierFieldConcepts.PROCEDURE_OCCURRENCE,
    )


def _procedure_link(event_id: int, episode_id: int) -> ExplicitLinkCase:
    return ExplicitLinkCase(
        event=ClinicalEventIdentity("procedure_occurrence", event_id),
        episode_id=episode_id,
        episode_event_field_concept_id=ModifierFieldConcepts.PROCEDURE_OCCURRENCE,
    )


def _attachment_rows(session, queries) -> set[tuple[int, int, str]]:
    rows = session.execute(queries.attachments).mappings().all()
    result = {
        (row["event_id"], row["episode_id"], row["attachment_method"]) for row in rows
    }
    assert len(result) == len(rows)
    return result


@pytest.mark.parametrize(
    "session_fixture",
    [
        "session",
        pytest.param("pg_session", marks=[pytest.mark.postgresql, pytest.mark.db_dialect]),
    ],
)
def test_ranked_fallback_admits_only_the_fallback_source(request, session_fixture):
    session = request.getfixturevalue(session_fixture)
    event = _procedure(8, date(2025, 7, 1))
    all_episodes = _episode_source(ROOT_EPISODE, CHILD_EPISODE)
    roots = _episode_source(ROOT_EPISODE, name="root_episodes")

    restricted = episode_attachment_queries(
        _event_source(event),
        explicit_episodes=all_episodes,
        fallback_episodes=roots,
        episode_events=_empty_link_source(),
        policy=EpisodeAttachmentPolicy.explicit_first_ranked,
        ranking=_nearest(started_first=True),
    )
    shared = episode_attachment_queries(
        _event_source(event),
        **_shared_episodes(all_episodes),
        episode_events=_empty_link_source(),
        policy=EpisodeAttachmentPolicy.explicit_first_ranked,
        ranking=_nearest(started_first=True),
    )

    assert _attachment_rows(session, restricted) == {(8, 3001, "fallback")}
    # With one shared population the later-starting child wins the ranking.
    assert _attachment_rows(session, shared) == {(8, 3002, "fallback")}


@pytest.mark.parametrize(
    "session_fixture",
    [
        "session",
        pytest.param("pg_session", marks=[pytest.mark.postgresql, pytest.mark.db_dialect]),
    ],
)
@pytest.mark.parametrize(
    "policy",
    [
        EpisodeAttachmentPolicy.explicit_first_ranked,
        EpisodeAttachmentPolicy.explicit_first_all_in_window,
    ],
)
def test_explicit_links_to_episodes_outside_the_fallback_source_are_kept(
    request, session_fixture, policy
):
    session = request.getfixturevalue(session_fixture)
    inside_root_window = _procedure(8, date(2025, 7, 1))
    outside_every_window = _procedure(9, date(2024, 6, 1))
    linked_twice = _procedure(10, date(2025, 7, 1))
    queries = episode_attachment_queries(
        _event_source(inside_root_window, outside_every_window, linked_twice),
        explicit_episodes=_episode_source(ROOT_EPISODE, CHILD_EPISODE),
        fallback_episodes=_episode_source(ROOT_EPISODE, name="root_episodes"),
        episode_events=_link_source(
            _procedure_link(8, 3002),
            _procedure_link(9, 3002),
            _procedure_link(10, 3001),
            _procedure_link(10, 3002),
        ),
        policy=policy,
        ranking=_nearest(started_first=True)
        if policy.requires_fallback_ranking
        else None,
    )

    assert _attachment_rows(session, queries) == {
        (8, 3002, "explicit"),
        (9, 3002, "explicit"),
        (10, 3001, "explicit"),
        (10, 3002, "explicit"),
    }


def test_event_eligible_only_outside_the_fallback_source_is_unattached(session):
    short_root = EpisodeCase(
        3001, person_id=101, start_date=date(2025, 1, 10), end_date=date(2025, 3, 31)
    )
    child_window_only = _procedure(8, date(2025, 8, 1))
    linked_to_child = _procedure(9, date(2025, 8, 1))
    queries = episode_attachment_queries(
        _event_source(child_window_only, linked_to_child),
        explicit_episodes=_episode_source(short_root, CHILD_EPISODE),
        fallback_episodes=_episode_source(short_root, name="root_episodes"),
        episode_events=_link_source(_procedure_link(9, 3002)),
        policy=EpisodeAttachmentPolicy.explicit_first_ranked,
        ranking=_nearest(started_first=True),
        include_diagnostics=True,
    )

    assert _attachment_rows(session, queries) == {(9, 3002, "explicit")}
    assert queries.diagnostics is not None
    diagnostics = session.execute(queries.diagnostics).mappings().all()
    assert [(row["diagnostic_code"], row["event_id"]) for row in diagnostics] == [
        (str(AttachmentDiagnosticCode.no_candidate_episode), 8)
    ]


def test_fallback_ambiguity_counts_only_fallback_candidates(session):
    second_root = EpisodeCase(
        3003, person_id=101, start_date=date(2025, 3, 1), end_date=date(2025, 12, 31)
    )
    queries = episode_attachment_queries(
        _event_source(_procedure(8, date(2025, 7, 1))),
        explicit_episodes=_episode_source(ROOT_EPISODE, CHILD_EPISODE, second_root),
        fallback_episodes=_episode_source(
            ROOT_EPISODE, second_root, name="root_episodes"
        ),
        episode_events=_empty_link_source(),
        policy=EpisodeAttachmentPolicy.explicit_first_ranked,
        ranking=_nearest(started_first=True),
        include_diagnostics=True,
    )

    assert _attachment_rows(session, queries) == {(8, 3003, "fallback")}
    assert queries.diagnostics is not None
    diagnostic = session.execute(queries.diagnostics).mappings().one()
    assert diagnostic["diagnostic_code"] == str(
        AttachmentDiagnosticCode.ambiguous_fallback
    )
    assert diagnostic["candidate_count"] == 2


@pytest.mark.parametrize(
    ("policy", "supply_fallback", "message"),
    [
        (EpisodeAttachmentPolicy.explicit_first_ranked, False, "requires fallback"),
        (EpisodeAttachmentPolicy.explicit_first_all_in_window, False, "requires"),
        (EpisodeAttachmentPolicy.explicit_only, True, "does not use fallback"),
    ],
)
def test_fallback_source_must_match_the_policy(policy, supply_fallback, message):
    episodes = _episode_source(OVERLAPPING_EPISODES[0])
    with pytest.raises(ValueError, match=message):
        episode_attachment_queries(
            _event_source(COLLIDING_EVENTS[0]),
            explicit_episodes=episodes,
            fallback_episodes=episodes if supply_fallback else None,
            episode_events=_empty_link_source(),
            policy=policy,
            ranking=_nearest() if policy.requires_fallback_ranking else None,
        )


def test_explicit_source_needs_only_episode_identity_and_person(session):
    identity_only = (
        sa.select(
            sa.literal(1002).label("episode_id"),
            sa.literal(101).label("person_id"),
        )
    ).cte("identity_only_episodes")
    queries = episode_attachment_queries(
        _event_source(COLLIDING_EVENTS[1]),
        explicit_episodes=identity_only,
        episode_events=_link_source(VALID_EXPLICIT_LINK),
        policy=EpisodeAttachmentPolicy.explicit_only,
    )

    assert session.execute(queries.attachments).mappings().one()["episode_id"] == 1002


def test_fallback_source_must_expose_date_bounds():
    without_end = (
        sa.select(
            sa.literal(1001).label("episode_id"),
            sa.literal(101).label("person_id"),
            sa.literal(date(2026, 1, 15)).label("episode_start_date"),
        )
    ).cte("episodes_without_end")
    with pytest.raises(InvalidAttachmentSourceError, match="fallback_episodes"):
        episode_attachment_queries(
            _event_source(COLLIDING_EVENTS[0]),
            explicit_episodes=_episode_source(OVERLAPPING_EPISODES[0]),
            fallback_episodes=without_end,
            episode_events=_empty_link_source(),
            policy=EpisodeAttachmentPolicy.explicit_first_all_in_window,
        )


def test_fallback_source_must_expose_the_ranking_stable_id():
    with pytest.raises(
        InvalidAttachmentSourceError,
        match="fallback_episodes is missing temporal stable ID column",
    ):
        episode_attachment_queries(
            _event_source(COLLIDING_EVENTS[0]),
            **_shared_episodes(_episode_source(OVERLAPPING_EPISODES[0])),
            episode_events=_empty_link_source(),
            policy=EpisodeAttachmentPolicy.explicit_first_ranked,
            ranking=TemporalRankingSpec(
                policy=TemporalSelectionPolicy.nearest,
                stable_id_column="episode_rank_id",
            ),
        )
