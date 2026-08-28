"""Query contracts and reusable counterexamples for SQL builders."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite

from omop_alchemy.cdm.base import ModifierFieldConcepts
from omop_alchemy.toolkit.core.concepts import (
    RuntimeConceptSetSpec,
)
from omop_alchemy.toolkit.core.events import (
    CANONICAL_EVENT_OPTIONAL_COLUMNS,
    CANONICAL_EVENT_REQUIRED_COLUMNS,
    ClinicalEventColumn,
    ClinicalEventIdentity,
    ClinicalEventRow,
)
from omop_alchemy.toolkit.core.timeline import Measurement_Event
from omop_alchemy.toolkit.episodes.derivation import (
    EpisodeAttachmentIdentity,
    EpisodeAttachmentPolicy,
    ObservationSelectionPolicy,
    ObservationSelectionSpec,
    TemporalRankingSpec,
    TemporalSelectionPolicy,
    TemporalSidePreference,
)
from tests.fixtures.query_contract_cases import (
    BOUNDARY_EVENTS,
    COLLIDING_EVENTS,
    CONSTRUCTS_EXACT_180_DAY_EPISODE,
    CONSTRUCTS_FUTURE_VISIT_EPISODES,
    CROSS_PERSON_LINK,
    DIRECTIONAL_PREFERENCE_EPISODES,
    OBSERVATION_ANCHOR_DATE,
    OVERLAPPING_EPISODES,
    REPEATED_OBSERVATIONS,
    VALID_EXPLICIT_LINK,
    WRONG_DISCRIMINATOR_LINK,
)


def test_canonical_event_shape_has_unique_stable_names():
    all_columns = CANONICAL_EVENT_REQUIRED_COLUMNS + CANONICAL_EVENT_OPTIONAL_COLUMNS

    assert len(all_columns) == len(set(all_columns))
    assert tuple(str(column) for column in CANONICAL_EVENT_REQUIRED_COLUMNS) == (
        "person_id",
        "event_id",
        "event_date",
        "event_datetime",
        "event_concept_id",
        "event_field_concept_id",
        "event_source_table",
    )


def test_source_table_is_required_to_distinguish_colliding_event_ids():
    identities = {case.identity for case in COLLIDING_EVENTS}

    assert {case.identity.event_id for case in COLLIDING_EVENTS} == {7}
    assert len(identities) == 3
    assert ClinicalEventIdentity("measurement", 7) != ClinicalEventIdentity(
        "procedure_occurrence", 7
    )


def test_timeline_event_implements_the_shared_core_event_contract():
    event = Measurement_Event(
        measurement_id=7,
        person_id=101,
        measurement_concept_id=900_001,
        measurement_date=date(2026, 1, 20),
        measurement_datetime=None,
    )

    assert isinstance(event, ClinicalEventRow)
    assert event.event_id == 7
    assert event.event_source_table == "measurement"
    assert event.event_field_concept_id == ModifierFieldConcepts.MEASUREMENT
    assert event.event_concept_id == 900_001
    assert event.event_date == date(2026, 1, 20)
    assert event.event_datetime is None


def test_attachment_identity_keeps_the_event_source_and_episode():
    event = ClinicalEventIdentity("procedure_occurrence", 7)

    first = EpisodeAttachmentIdentity.from_event(event, episode_id=1001)
    second = EpisodeAttachmentIdentity.from_event(event, episode_id=1002)

    assert first.event == event
    assert first != second


@pytest.mark.parametrize("identity_type", [ClinicalEventIdentity, EpisodeAttachmentIdentity])
def test_event_source_table_cannot_be_empty(identity_type):
    args = ("", 7) if identity_type is ClinicalEventIdentity else ("", 7, 1001)
    with pytest.raises(ValueError, match="event_source_table"):
        identity_type(*args)


@pytest.mark.parametrize(
    ("policy", "uses_fallback", "permits_fanout"),
    [
        (EpisodeAttachmentPolicy.explicit_only, False, False),
        (EpisodeAttachmentPolicy.explicit_first_ranked, True, False),
        (EpisodeAttachmentPolicy.explicit_first_all_in_window, True, True),
    ],
)
def test_attachment_policies_state_precedence_and_cardinality(
    policy: EpisodeAttachmentPolicy,
    uses_fallback: bool,
    permits_fanout: bool,
):
    assert policy.uses_fallback is uses_fallback
    assert policy.permits_fallback_fanout is permits_fanout
    assert policy.requires_fallback_ranking is (
        policy is EpisodeAttachmentPolicy.explicit_first_ranked
    )


def test_counterexample_links_cover_valid_discriminator_and_person_failures():
    event_by_identity = {case.identity: case for case in COLLIDING_EVENTS}
    episode_by_id = {case.episode_id: case for case in OVERLAPPING_EPISODES}

    valid_event = event_by_identity[VALID_EXPLICIT_LINK.event]
    assert valid_event.event_field_concept_id == VALID_EXPLICIT_LINK.episode_event_field_concept_id
    assert valid_event.person_id == episode_by_id[VALID_EXPLICIT_LINK.episode_id].person_id

    wrong_event = event_by_identity[WRONG_DISCRIMINATOR_LINK.event]
    assert (
        wrong_event.event_field_concept_id
        != WRONG_DISCRIMINATOR_LINK.episode_event_field_concept_id
    )

    cross_person_event = event_by_identity[CROSS_PERSON_LINK.event]
    assert cross_person_event.person_id != episode_by_id[CROSS_PERSON_LINK.episode_id].person_id


def test_nearest_temporal_contract_uses_absolute_distance_and_stable_id():
    spec = TemporalRankingSpec(
        policy=TemporalSelectionPolicy.nearest,
        stable_id_column="episode_id",
    )
    event_date = date(2026, 1, 20)
    ranked = sorted(
        OVERLAPPING_EPISODES[:2],
        key=lambda episode: (
            abs((event_date - episode.start_date).days),
            episode.episode_id,
        ),
    )

    assert spec.uses_absolute_distance
    assert [episode.episode_id for episode in ranked] == [1001, 1002]


def test_started_episode_preference_can_override_absolute_nearest():
    anchor = date(2026, 1, 20)
    neutral = TemporalRankingSpec(
        policy=TemporalSelectionPolicy.nearest,
        stable_id_column="episode_id",
    )
    started_first = TemporalRankingSpec(
        policy=TemporalSelectionPolicy.nearest,
        stable_id_column="episode_id",
        side_preference=TemporalSidePreference.on_or_before_anchor,
    )

    absolute = sorted(
        DIRECTIONAL_PREFERENCE_EPISODES,
        key=lambda episode: (abs((episode.start_date - anchor).days), episode.episode_id),
    )
    directional = sorted(
        DIRECTIONAL_PREFERENCE_EPISODES,
        key=lambda episode: (
            episode.start_date > anchor,
            abs((episode.start_date - anchor).days),
            episode.episode_id,
        ),
    )

    assert not neutral.has_side_preference
    assert started_first.has_side_preference
    assert absolute[0].episode_id == 1003
    assert directional[0].episode_id == 1001


def test_constructs_visit_fixture_exposes_signed_future_distance_pitfall():
    event_date = date(2026, 1, 20)
    signed = sorted(
        CONSTRUCTS_FUTURE_VISIT_EPISODES,
        key=lambda episode: (event_date - episode.start_date).days,
    )
    absolute = sorted(
        CONSTRUCTS_FUTURE_VISIT_EPISODES,
        key=lambda episode: abs((event_date - episode.start_date).days),
    )

    assert signed[0].episode_id == 4002
    assert absolute[0].episode_id == 4001


def test_constructs_visit_fixture_records_strict_180_day_boundary():
    event_date = date(2026, 1, 20)
    distance = abs((event_date - CONSTRUCTS_EXACT_180_DAY_EPISODE.start_date).days)

    assert distance == 180
    assert not distance < 180


def test_boundary_fixture_makes_closed_window_expectations_visible():
    spec = TemporalRankingSpec(
        policy=TemporalSelectionPolicy.nearest,
        stable_id_column="event_id",
    )
    episode = OVERLAPPING_EPISODES[0]
    lower = episode.start_date - timedelta(days=90)
    upper = episode.end_date
    assert upper is not None
    included = [
        event.identity.event_id
        for event in BOUNDARY_EVENTS
        if lower <= event.event_date <= upper
    ]

    assert spec.include_lower_bound
    assert spec.include_upper_bound
    assert included == [8, 10]


def test_observation_as_of_contract_excludes_future_and_breaks_ties_by_id():
    spec = ObservationSelectionSpec(
        policy=ObservationSelectionPolicy.latest_on_or_before_anchor
    )
    candidates = [
        row for row in REPEATED_OBSERVATIONS if row.observation_date <= OBSERVATION_ANCHOR_DATE
    ]
    selected = sorted(
        candidates,
        key=lambda row: (-row.observation_date.toordinal(), row.observation_id),
    )[0]

    assert spec.requires_anchor
    assert selected.observation_id == 22


def test_runtime_concept_set_is_normalised_without_database_access():
    spec = RuntimeConceptSetSpec(
        include_ancestor_ids=(300, 100, 300),
        include_exact_ids=(900,),
        exclude_ancestor_ids=(400,),
        exclude_exact_ids=(901, 901),
        require_standard=True,
        include_classification=False,
    )

    assert spec.include_ancestor_ids == (100, 300)
    assert spec.exclude_exact_ids == (901,)
    assert spec.has_inclusions
    assert spec.requires_concept_join


def test_runtime_concept_set_does_not_invent_concept_id_validity_policy():
    spec = RuntimeConceptSetSpec(include_exact_ids=(0, -1, 0))

    assert spec.include_exact_ids == (-1, 0)


def _projection_contract_select() -> sa.Select:
    """Minimal selectable proving the canonical labels compile on supported dialects."""
    event = sa.table(
        "research_event",
        sa.column("person_id", sa.Integer),
        sa.column("event_id", sa.Integer),
        sa.column("event_date", sa.Date),
        sa.column("event_concept_id", sa.Integer),
    )
    return sa.select(
        event.c.person_id.label(ClinicalEventColumn.person_id),
        event.c.event_id.label(ClinicalEventColumn.event_id),
        event.c.event_date.label(ClinicalEventColumn.event_date),
        sa.cast(sa.null(), sa.DateTime).label(ClinicalEventColumn.event_datetime),
        event.c.event_concept_id.label(ClinicalEventColumn.event_concept_id),
        sa.literal(ModifierFieldConcepts.MEASUREMENT).label(
            ClinicalEventColumn.event_field_concept_id
        ),
        sa.literal("measurement").label(ClinicalEventColumn.event_source_table),
    )


@pytest.mark.parametrize("dialect", [sqlite.dialect(), postgresql.dialect()])
def test_required_projection_contract_compiles_without_execution(dialect):
    statement = _projection_contract_select()
    compiled = str(statement.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))

    assert tuple(statement.selected_columns.keys()) == tuple(
        str(column) for column in CANONICAL_EVENT_REQUIRED_COLUMNS
    )
    assert "event_field_concept_id" in compiled
    assert "event_source_table" in compiled
