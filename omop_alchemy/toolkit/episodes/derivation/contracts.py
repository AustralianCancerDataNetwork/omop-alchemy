"""Side-effect-free contracts for episode attachment and temporal SQL builders.

The shared clinical-event row and identity contracts live in
``toolkit.core.events`` so timelines and episode builders can consume them
without reversing the toolkit import layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from omop_alchemy.toolkit.core.events import ClinicalEventIdentity


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


class AttachmentDiagnosticCode(StrEnum):
    """Stable categories for explaining rejected or ambiguous attachment rows."""

    discriminator_mismatch = "discriminator_mismatch"
    person_mismatch = "person_mismatch"
    dangling_event = "dangling_event"
    no_candidate_episode = "no_candidate_episode"
    ambiguous_fallback = "ambiguous_fallback"


@dataclass(frozen=True, slots=True)
class EpisodeAttachmentDiagnostic:
    """Advisory result explaining why an attachment needs review."""

    code: AttachmentDiagnosticCode
    event: ClinicalEventIdentity
    message: str
    episode_id: int | None = None


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
    """Temporal ranking and boundary contract for future SQL builders.

    A side preference, when present, is applied before the selection policy.
    ``nearest`` then means the smallest absolute distance within that tier.
    All policies use the named stable ID column as their final ascending
    tie-breaker, so the same source rows cannot alternate across executions.
    """

    policy: TemporalSelectionPolicy
    stable_id_column: str
    side_preference: TemporalSidePreference = TemporalSidePreference.none
    include_lower_bound: bool = True
    include_upper_bound: bool = True

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
