"""Side-effect-free contracts for episode attachment and temporal SQL builders.

The shared clinical-event row and identity contracts live in
``toolkit.core.events`` so timelines and episode builders can consume them
without reversing the toolkit import layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from omop_alchemy.toolkit.core.events import ClinicalEventIdentity
from omop_alchemy.toolkit.episodes.handling.event_windowing import (
    DEFAULT_EPISODE_OPEN_END_FALLBACK_DAYS,
    DEFAULT_EPISODE_WINDOW_DAYS_PRIOR,
)


class EpisodeColumn(StrEnum):
    """Canonical labels emitted by an episode projection."""

    episode_id = "episode_id"
    person_id = "person_id"
    episode_parent_id = "episode_parent_id"
    episode_start_date = "episode_start_date"
    episode_start_datetime = "episode_start_datetime"
    episode_end_date = "episode_end_date"
    episode_end_datetime = "episode_end_datetime"
    episode_concept_id = "episode_concept_id"
    episode_object_concept_id = "episode_object_concept_id"
    episode_type_concept_id = "episode_type_concept_id"
    episode_concept_name = "episode_concept_name"


CANONICAL_EPISODE_COLUMNS: tuple[EpisodeColumn, ...] = tuple(
    column
    for column in EpisodeColumn
    if column is not EpisodeColumn.episode_concept_name
)
"""Columns exposed by the canonical episode projection."""

CANONICAL_EPISODE_OPTIONAL_COLUMNS: tuple[EpisodeColumn, ...] = (
    EpisodeColumn.episode_concept_name,
)
"""Opt-in descriptive columns exposed by an enriched episode projection."""


@dataclass(frozen=True, order=True, slots=True)
class EpisodeAttachmentIdentity:
    """Unique identity of one event attached to one episode."""

    event_source_table: str
    event_id: int
    episode_id: int

    def __post_init__(self) -> None:
        # Keep the directly constructed form as safe as ``from_event``: downstream
        # result sets use this key for deduplication, so an empty source would erase
        # the boundary that protects against cross-table ID collisions.
        ClinicalEventIdentity(self.event_source_table, self.event_id)

    @classmethod
    def from_event(
        cls,
        event: ClinicalEventIdentity,
        *,
        episode_id: int,
    ) -> EpisodeAttachmentIdentity:
        """Add an episode to an already canonical cross-table event identity."""
        return cls(
            event_source_table=event.event_source_table,
            event_id=event.event_id,
            episode_id=episode_id,
        )

    @property
    def event(self) -> ClinicalEventIdentity:
        """The event portion of this attachment identity."""
        return ClinicalEventIdentity(self.event_source_table, self.event_id)


class EpisodeAttachmentPolicy(StrEnum):
    """Named precedence and fallback-cardinality policies.

    A valid explicit link always wins in the two explicit-first policies. The
    difference is what happens to an event that has no valid explicit link.
    """

    explicit_only = "explicit_only"
    explicit_first_ranked = "explicit_first_ranked"
    explicit_first_all_in_window = "explicit_first_all_in_window"

    @property
    def uses_fallback(self) -> bool:
        """Whether unlinked events may be attached by a date window."""
        return self is not EpisodeAttachmentPolicy.explicit_only

    @property
    def permits_fallback_fanout(self) -> bool:
        """Whether one fallback event may attach to several episodes."""
        return self is EpisodeAttachmentPolicy.explicit_first_all_in_window

    @property
    def requires_fallback_ranking(self) -> bool:
        """Whether fallback needs a separate temporal ranking specification."""
        return self is EpisodeAttachmentPolicy.explicit_first_ranked


class EpisodeAttachmentMethod(StrEnum):
    """How an event-to-episode attachment was established."""

    explicit = "explicit"
    fallback = "fallback"


class AttachmentDiagnosticCode(StrEnum):
    """Stable categories for explaining rejected or ambiguous attachment rows."""

    person_mismatch = "person_mismatch"
    no_candidate_episode = "no_candidate_episode"
    ambiguous_fallback = "ambiguous_fallback"


class AttachmentDiagnosticColumn(StrEnum):
    """Stable labels emitted by an attachment diagnostics query."""

    diagnostic_code = "diagnostic_code"
    event_source_table = "event_source_table"
    event_id = "event_id"
    event_field_concept_id = "event_field_concept_id"
    linked_event_field_concept_id = "linked_event_field_concept_id"
    episode_id = "episode_id"
    candidate_count = "candidate_count"
    message = "message"


CANONICAL_ATTACHMENT_DIAGNOSTIC_COLUMNS: tuple[AttachmentDiagnosticColumn, ...] = tuple(
    AttachmentDiagnosticColumn
)
"""Columns exposed by an attachment diagnostics query."""


@dataclass(frozen=True, slots=True)
class EpisodeAttachmentDiagnostic:
    """Typed advisory result returned by an attachment diagnostics query."""

    code: AttachmentDiagnosticCode
    event: ClinicalEventIdentity
    event_field_concept_id: int
    message: str
    linked_event_field_concept_id: int | None = None
    episode_id: int | None = None
    candidate_count: int | None = None

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "EpisodeAttachmentDiagnostic":
        """Convert one SQLAlchemy mapping result without leaking column-name handling."""
        return cls(
            code=AttachmentDiagnosticCode(row["diagnostic_code"]),
            event=ClinicalEventIdentity(
                event_source_table=row["event_source_table"],
                event_id=row["event_id"],
            ),
            event_field_concept_id=row["event_field_concept_id"],
            linked_event_field_concept_id=row["linked_event_field_concept_id"],
            episode_id=row["episode_id"],
            candidate_count=row["candidate_count"],
            message=row["message"],
        )


@dataclass(frozen=True, slots=True)
class EpisodeWindowSpec:
    """Finite episode-relative window used to admit fallback event candidates."""

    days_prior: int = DEFAULT_EPISODE_WINDOW_DAYS_PRIOR
    open_end_fallback_days: int = DEFAULT_EPISODE_OPEN_END_FALLBACK_DAYS
    include_lower_bound: bool = True
    include_upper_bound: bool = True

    def __post_init__(self) -> None:
        if self.days_prior < 0:
            raise ValueError("days_prior must be non-negative")
        if self.open_end_fallback_days < 0:
            raise ValueError("open_end_fallback_days must be non-negative")


class TemporalSelectionPolicy(StrEnum):
    """How one row is selected from several temporal candidates."""

    nearest = "nearest"
    earliest = "earliest"
    latest = "latest"


class TemporalSidePreference(StrEnum):
    """Which side of an anchor is preferred before temporal ranking.

    Candidate dates are ranked relative to a caller-supplied anchor. For event
    attachment where the event is the anchor and episode starts are candidates,
    ``on_or_before_anchor`` prefers an episode that had already started.
    """

    none = "none"
    on_or_before_anchor = "on_or_before_anchor"
    on_or_after_anchor = "on_or_after_anchor"


@dataclass(frozen=True, slots=True)
class TemporalRankingSpec:
    """Temporal ranking contract for SQL builders.

    A side preference, when present, is applied before the selection policy.
    ``nearest`` then means the smallest absolute distance within that tier.
    All policies use the named stable ID column as their final ascending
    tie-breaker, so the same source rows cannot alternate across executions.
    """

    policy: TemporalSelectionPolicy
    stable_id_column: str
    side_preference: TemporalSidePreference = TemporalSidePreference.none

    def __post_init__(self) -> None:
        if not self.stable_id_column.strip():
            raise ValueError("stable_id_column must not be empty")

    @property
    def uses_absolute_distance(self) -> bool:
        """Whether ranking uses absolute distance after any side-preference tier."""
        return self.policy is TemporalSelectionPolicy.nearest

    @property
    def has_side_preference(self) -> bool:
        """Whether candidates are tiered by their side of the anchor first."""
        return self.side_preference is not TemporalSidePreference.none


class ObservationSelectionPolicy(StrEnum):
    """Supported deterministic choices for repeated longitudinal observations."""

    earliest = "earliest"
    latest = "latest"
    latest_on_or_before_anchor = "latest_on_or_before_anchor"


@dataclass(frozen=True, slots=True)
class ObservationSelectionSpec:
    """Partition and tie-break contract for repeated-observation selection.

    The default partition describes one observation concept for one person.
    Callers may add an episode or another grouping field when their declared
    result grain requires it.
    """

    policy: ObservationSelectionPolicy
    partition_by: tuple[str, ...] = ("person_id", "observation_concept_id")
    stable_id_column: str = "observation_id"
    include_anchor_date: bool = True

    def __post_init__(self) -> None:
        if not self.partition_by:
            raise ValueError("partition_by must contain at least one column")
        if any(not name.strip() for name in self.partition_by):
            raise ValueError("partition_by column names must not be empty")
        if len(set(self.partition_by)) != len(self.partition_by):
            raise ValueError("partition_by column names must be unique")
        if not self.stable_id_column.strip():
            raise ValueError("stable_id_column must not be empty")

    @property
    def requires_anchor(self) -> bool:
        """Whether selection requires a caller-supplied anchor date."""
        return self.policy is ObservationSelectionPolicy.latest_on_or_before_anchor
