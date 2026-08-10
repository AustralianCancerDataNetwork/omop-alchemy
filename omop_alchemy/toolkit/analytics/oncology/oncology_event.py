from __future__ import annotations

from typing import Any

import sqlalchemy.orm as so
from sqlalchemy.ext.declarative import declared_attr

from omop_alchemy.cdm.base import ModifierFieldConcepts
from omop_alchemy.toolkit.episodes.handling import ResolvedEpisodeEvent
from omop_alchemy.toolkit.episodes.handling.resolved_event import (
    _episode_events_relationship,
)

from .oncology_drug_exposure import OncologyDrugExposure
from .oncology_procedure_occurrence import OncologyProcedure


class OncologyEpisodeEvent(ResolvedEpisodeEvent):
    """
    Episode-event view that prefers oncology-aware fact table views.

    Non-oncology targets continue to use the base resolver map from
    ``ResolvedEpisodeEvent``. Procedure and drug targets resolve to oncology
    subclasses so episode traversal can ask domain-specific questions such as
    ``is_radiotherapy`` and ``is_sact`` — and, since this extends
    ``ResolvedEpisodeEvent`` rather than the bare ``Episode_EventView``,
    ``event_resolution_diagnostics`` is available on oncology events too.
    """

    @classmethod
    def resolved_event_target_classes(cls) -> dict[int, type[Any]]:
        base_resolver = getattr(ResolvedEpisodeEvent, "resolved_event_target_classes")
        targets = dict(base_resolver())
        targets.update(
            {
                ModifierFieldConcepts.PROCEDURE_OCCURRENCE: OncologyProcedure,
                ModifierFieldConcepts.DRUG_EXPOSURE: OncologyDrugExposure,
            }
        )
        return targets


class OncologyEpisodeEventMixin:
    """
    Relationship override for episode classes that need oncology event targets.
    """

    @declared_attr
    @classmethod
    def episode_events(cls) -> so.Mapped[list["OncologyEpisodeEvent"]]:
        return _episode_events_relationship(cls, OncologyEpisodeEvent)
