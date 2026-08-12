from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeVar, cast

import sqlalchemy.orm as so
from sqlalchemy.ext.declarative import declared_attr

from omop_alchemy.cdm.base import ModifierFieldConcepts
from omop_alchemy.cdm.model.structural.episode_event import Episode_EventView

ResolutionDiagnosticKind = Literal[
    "unrecognized_field_concept",
    "unmapped_field_concept",
    "dangling_event",
]

EpisodeEventT = TypeVar("EpisodeEventT", bound="ResolvedEpisodeEvent")


@dataclass(frozen=True)
class EpisodeEventResolutionDiagnostic:
    """
    Advisory detail for an episode_event row whose target cannot be resolved.

    Resolution remains best-effort: unresolved rows still return ``None`` from
    ``resolved_event``. These diagnostics give maintenance and QA code enough
    information to distinguish miscoded field concepts from harmless ORM
    coverage gaps and genuine dangling event references.
    """

    kind: ResolutionDiagnosticKind
    episode_id: int
    event_id: int
    episode_event_field_concept_id: int
    message: str


def _known_modifier_field_concept_ids() -> set[int]:
    return {
        value
        for name, value in vars(ModifierFieldConcepts).items()
        if name.isupper() and isinstance(value, int)
    }


class ResolvedEpisodeEvent(Episode_EventView):
    """
    Episode-event view that explains why a link did not resolve.

    ``Episode_EventView.resolved_event`` already resolves best-effort and
    returns ``None`` on failure. ``event_resolution_diagnostics`` names which
    of three reasons applied: the field concept is not a recognised
    ``ModifierFieldConcepts`` value, it is recognised but no registered ORM
    class declares it, or the target row itself is missing.

    Query this class directly for diagnostics on a known episode_event, or
    mix ``ResolvedEpisodeEventMixin`` into an episode view to reach it
    through ordinary ``episode.episode_events`` traversal.
    """

    @classmethod
    def recognized_field_concept_ids(cls) -> set[int]:
        return _known_modifier_field_concept_ids()

    @property
    def event_resolution_diagnostics(self) -> list[EpisodeEventResolutionDiagnostic]:
        session = so.object_session(self)
        field_concept_id = self.episode_event_field_concept_id

        if field_concept_id not in self.recognized_field_concept_ids():
            return [
                EpisodeEventResolutionDiagnostic(
                    kind="unrecognized_field_concept",
                    episode_id=self.episode_id,
                    event_id=self.event_id,
                    episode_event_field_concept_id=field_concept_id,
                    message=(
                        "episode_event_field_concept_id is not a known "
                        "ModifierFieldConcepts value"
                    ),
                )
            ]

        target_cls = self.resolved_event_class
        if target_cls is None:
            return [
                EpisodeEventResolutionDiagnostic(
                    kind="unmapped_field_concept",
                    episode_id=self.episode_id,
                    event_id=self.event_id,
                    episode_event_field_concept_id=field_concept_id,
                    message=(
                        "episode_event_field_concept_id is recognized, but no "
                        "registered ORM target class declares it"
                    ),
                )
            ]

        if session is None:
            return []

        if session.get(target_cls, self.event_id) is None:
            return [
                EpisodeEventResolutionDiagnostic(
                    kind="dangling_event",
                    episode_id=self.episode_id,
                    event_id=self.event_id,
                    episode_event_field_concept_id=field_concept_id,
                    message=(
                        f"episode_event points to {target_cls.__name__}#{self.event_id}, "
                        "but no matching row exists"
                    ),
                )
            ]

        return []


def _episode_events_relationship(
    owner_cls: type[Any],
    target_cls: type[EpisodeEventT],
) -> so.Mapped[list[EpisodeEventT]]:
    """Build the view-only episode-event relationship used by episode mixins."""
    owner_episode_id = cast(Any, owner_cls).__table__.c.episode_id
    event_episode_id = target_cls.__table__.c.episode_id
    return so.relationship(
        target_cls,
        primaryjoin=lambda: owner_episode_id == event_episode_id,
        viewonly=True,
        lazy="selectin",
    )


class ResolvedEpisodeEventMixin:
    """
    Relationship override so episode views can traverse to resolution diagnostics.

    Mix into an ``EpisodeContext``-derived view to have ``episode_events``
    load ``ResolvedEpisodeEvent`` rows instead of the bare
    ``Episode_EventView``, making
    ``episode.episode_events[0].event_resolution_diagnostics`` reachable
    through ordinary traversal rather than a direct query.
    """

    @declared_attr
    @classmethod
    def episode_events(cls) -> so.Mapped[list["ResolvedEpisodeEvent"]]:
        return _episode_events_relationship(cls, ResolvedEpisodeEvent)
