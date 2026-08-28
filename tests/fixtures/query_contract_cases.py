"""Counterexamples that clinical query builders must satisfy.

The IDs are intentionally small and collide across event tables. Dates are
chosen so precedence, boundaries, and ties can be verified by inspection.
This module contains data only; it does not encode the expected algorithm.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from omop_alchemy.cdm.base import ModifierFieldConcepts
from omop_alchemy.toolkit.core.events import ClinicalEventIdentity

MEASUREMENT_FIELD_CONCEPT_ID = ModifierFieldConcepts.MEASUREMENT
OBSERVATION_FIELD_CONCEPT_ID = ModifierFieldConcepts.OBSERVATION
PROCEDURE_FIELD_CONCEPT_ID = ModifierFieldConcepts.PROCEDURE_OCCURRENCE


@dataclass(frozen=True, slots=True)
class EventCase:
    identity: ClinicalEventIdentity
    person_id: int
    event_date: date
    event_field_concept_id: int


@dataclass(frozen=True, slots=True)
class EpisodeCase:
    episode_id: int
    person_id: int
    start_date: date
    end_date: date | None


@dataclass(frozen=True, slots=True)
class ExplicitLinkCase:
    event: ClinicalEventIdentity
    episode_id: int
    episode_event_field_concept_id: int


@dataclass(frozen=True, slots=True)
class ObservationCase:
    observation_id: int
    person_id: int
    observation_concept_id: int
    observation_date: date
    value: str


# Numeric event ID 7 exists in two event tables for the same person. A third
# event with ID 7 belongs to another person. Numeric ID alone cannot identify
# any of these rows safely.
COLLIDING_EVENTS = (
    EventCase(
        ClinicalEventIdentity("measurement", 7),
        person_id=101,
        event_date=date(2026, 1, 20),
        event_field_concept_id=MEASUREMENT_FIELD_CONCEPT_ID,
    ),
    EventCase(
        ClinicalEventIdentity("procedure_occurrence", 7),
        person_id=101,
        event_date=date(2026, 1, 20),
        event_field_concept_id=PROCEDURE_FIELD_CONCEPT_ID,
    ),
    EventCase(
        ClinicalEventIdentity("observation", 7),
        person_id=202,
        event_date=date(2026, 1, 20),
        event_field_concept_id=OBSERVATION_FIELD_CONCEPT_ID,
    ),
)


# Episodes 1001 and 1002 overlap. Their start dates are equally distant from
# 20 January, so nearest selection must use episode_id as its final tie-break.
OVERLAPPING_EPISODES = (
    EpisodeCase(1001, person_id=101, start_date=date(2026, 1, 15), end_date=date(2026, 2, 5)),
    EpisodeCase(1002, person_id=101, start_date=date(2026, 1, 25), end_date=date(2026, 2, 20)),
    EpisodeCase(2001, person_id=202, start_date=date(2026, 1, 10), end_date=None),
)


# For the 20 January event, episode 1003 is technically closer but has not
# started. A side-neutral nearest policy selects 1003; a policy that prefers
# already-started episodes selects 1001.
DIRECTIONAL_PREFERENCE_EPISODES = (
    EpisodeCase(1001, person_id=101, start_date=date(2026, 1, 15), end_date=date(2026, 2, 5)),
    EpisodeCase(1003, person_id=101, start_date=date(2026, 1, 21), end_date=date(2026, 2, 28)),
)


# These future-only candidates reproduce the signed-distance pitfall in the
# current omop-constructs visit ranking. With diff = event - episode start,
# ascending signed values choose 4002 even though 4001 is closer.
CONSTRUCTS_FUTURE_VISIT_EPISODES = (
    EpisodeCase(4001, person_id=101, start_date=date(2026, 8, 1), end_date=None),
    EpisodeCase(4002, person_id=101, start_date=date(2026, 9, 1), end_date=None),
)

# Current omop-constructs uses abs(diff_days) < 180 for its first visit tier.
# This start is exactly 180 days before the event and therefore exposes the
# strict-boundary behaviour.
CONSTRUCTS_EXACT_180_DAY_EPISODE = EpisodeCase(
    4003,
    person_id=101,
    start_date=date(2025, 7, 24),
    end_date=None,
)


VALID_EXPLICIT_LINK = ExplicitLinkCase(
    event=ClinicalEventIdentity("procedure_occurrence", 7),
    episode_id=1002,
    episode_event_field_concept_id=PROCEDURE_FIELD_CONCEPT_ID,
)

WRONG_DISCRIMINATOR_LINK = ExplicitLinkCase(
    event=ClinicalEventIdentity("procedure_occurrence", 7),
    episode_id=1001,
    episode_event_field_concept_id=MEASUREMENT_FIELD_CONCEPT_ID,
)

CROSS_PERSON_LINK = ExplicitLinkCase(
    event=ClinicalEventIdentity("observation", 7),
    episode_id=1001,
    episode_event_field_concept_id=OBSERVATION_FIELD_CONCEPT_ID,
)


# With a 90-day prior window and episode 1001 starting on 15 January, 17
# October is the inclusive lower boundary and 16 October is outside it.
BOUNDARY_EVENTS = (
    EventCase(
        ClinicalEventIdentity("measurement", 8),
        person_id=101,
        event_date=date(2025, 10, 17),
        event_field_concept_id=MEASUREMENT_FIELD_CONCEPT_ID,
    ),
    EventCase(
        ClinicalEventIdentity("measurement", 9),
        person_id=101,
        event_date=date(2025, 10, 16),
        event_field_concept_id=MEASUREMENT_FIELD_CONCEPT_ID,
    ),
    EventCase(
        ClinicalEventIdentity("measurement", 10),
        person_id=101,
        event_date=date(2026, 2, 5),
        event_field_concept_id=MEASUREMENT_FIELD_CONCEPT_ID,
    ),
    EventCase(
        ClinicalEventIdentity("measurement", 11),
        person_id=101,
        event_date=date(2026, 2, 6),
        event_field_concept_id=MEASUREMENT_FIELD_CONCEPT_ID,
    ),
)


# Two observations occur on the anchor date. Stable ascending observation_id
# makes 22 the deterministic winner for latest-on-or-before-anchor.
OBSERVATION_ANCHOR_DATE = date(2026, 1, 20)
REPEATED_OBSERVATIONS = (
    ObservationCase(21, 101, 900_001, date(2026, 1, 1), "earlier"),
    ObservationCase(22, 101, 900_001, OBSERVATION_ANCHOR_DATE, "anchor-a"),
    ObservationCase(23, 101, 900_001, OBSERVATION_ANCHOR_DATE, "anchor-b"),
    ObservationCase(24, 101, 900_001, date(2026, 1, 21), "after-anchor"),
)
