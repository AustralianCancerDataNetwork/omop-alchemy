# pyright: reportAssignmentType=false
from __future__ import annotations

from enum import StrEnum
from functools import cached_property
from typing import Self, cast

import sqlalchemy.orm as so
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import object_session

from omop_alchemy.cdm.model.structural import EpisodeView

from .concept_sets import (
    disease_episode_type_concept_ids,
    overarching_episode_type_concept_id,
    treatment_cycle_episode_concept_id,
    treatment_episode_type_concept_ids,
    treatment_regimen_episode_concept_id,
)
from .oncology_critical_weight_loss import OncologyCriticalWeightLossMixin
from .oncology_drug_exposure import OncologyDrugExposure
from .oncology_event import OncologyEpisodeEventMixin
from .oncology_procedure_occurrence import OncologyProcedure
from .oncology_rt_dosing import OncologyRTDosingMixin
from .oncology_sact_dosing import OncologySACTDosingMixin
from .session_guard import require_session


class OncologyModality(StrEnum):
    SACT = "sact"
    RADIOTHERAPY = "radiotherapy"
    SURGERY = "surgery"
    DIAGNOSTIC_STAGING = "diagnostic_staging"
    UNKNOWN = "unknown"


# Deterministic tie-breaker, not a clinical priority: when an episode contains
# evidence for multiple modalities, prefer events that more reliably indicate a
# treatment modality rather than depending on database return order.
_MODALITY_PRIORITY: tuple[OncologyModality, ...] = (
    OncologyModality.RADIOTHERAPY,
    OncologyModality.SURGERY,
    OncologyModality.DIAGNOSTIC_STAGING,
    OncologyModality.SACT,
)


def _preferred_modality(modalities: frozenset[OncologyModality]) -> OncologyModality:
    return next(
        (modality for modality in _MODALITY_PRIORITY if modality in modalities),
        OncologyModality.UNKNOWN,
    )


class OncologyEpisode(
    OncologyCriticalWeightLossMixin,
    OncologySACTDosingMixin,
    OncologyRTDosingMixin,
    OncologyEpisodeEventMixin,
    EpisodeView,
):
    """
    Oncology-aware episode view.

    This composes generic episode hierarchy support, oncology-aware
    ``Episode_Event`` resolution, body-metric adverse-event grading, and
    treatment dose-summary interfaces. Modality classification exposes both
    structural treatment evidence, such as linked drug exposures, and governed
    concept evidence, such as SACT-classified drug concepts, so callers can
    audit disagreements.
    """

    @declared_attr
    @classmethod
    def children(cls) -> so.Mapped[list["OncologyEpisode"]]:
        # Not redundant with EpisodeContext.children: that declared_attr fires only
        # for EpisodeView, so inheriting it would give OncologyEpisode children
        # typed as EpisodeView. Redeclaring re-runs it with cls=OncologyEpisode.
        episode_id = cls.__table__.c.episode_id
        episode_parent_id = cls.__table__.c.episode_parent_id
        return so.relationship(
            cls.__name__,
            primaryjoin=lambda: so.remote(episode_parent_id) == episode_id,
            foreign_keys=lambda: [episode_parent_id],
            remote_side=lambda: [episode_parent_id],
            viewonly=True,
            lazy="selectin",
            uselist=True,
        )

    @hybrid_property
    def is_disease_episode(self) -> bool:
        return self.episode_concept_id in disease_episode_type_concept_ids()

    @is_disease_episode.inplace.expression
    @classmethod
    def _is_disease_episode_expression(cls):
        return cls.episode_concept_id.in_(disease_episode_type_concept_ids())

    @hybrid_property
    def is_overarching(self) -> bool:
        return self.episode_concept_id == overarching_episode_type_concept_id()

    @is_overarching.inplace.expression
    @classmethod
    def _is_overarching_expression(cls):
        return cls.episode_concept_id == overarching_episode_type_concept_id()

    @hybrid_property
    def is_treatment_episode(self) -> bool:
        return self.episode_concept_id in treatment_episode_type_concept_ids()

    @is_treatment_episode.inplace.expression
    @classmethod
    def _is_treatment_episode_expression(cls):
        return cls.episode_concept_id.in_(treatment_episode_type_concept_ids())

    @hybrid_property
    def is_treatment_regimen(self) -> bool:
        return self.episode_concept_id == treatment_regimen_episode_concept_id()

    @is_treatment_regimen.inplace.expression
    @classmethod
    def _is_treatment_regimen_expression(cls):
        return cls.episode_concept_id == treatment_regimen_episode_concept_id()

    @hybrid_property
    def is_treatment_cycle(self) -> bool:
        return self.episode_concept_id == treatment_cycle_episode_concept_id()

    @is_treatment_cycle.inplace.expression
    @classmethod
    def _is_treatment_cycle_expression(cls):
        return cls.episode_concept_id == treatment_cycle_episode_concept_id()

    @property
    def primary_episode(self) -> Self:
        current = self
        while not current.is_overarching and current.episode_parent_id is not None:
            session = object_session(current)
            if session is None:
                break
            parent = session.get(type(self), current.episode_parent_id)
            if parent is None:
                break
            current = parent
        return current

    @cached_property
    def child_treatment_episodes(self) -> list[Self]:
        return [
            child
            for child in cast(list[Self], self.children)
            if child.is_treatment_episode
        ]

    @cached_property
    def _linked_oncology_events(self) -> list[object]:
        """Resolved events linked to this episode or one of its direct children."""
        require_session(self)
        resolved: list[object] = list(self.events)
        for child in cast(list[Self], self.children):
            resolved.extend(child.events)
        return resolved

    @cached_property
    def structural_modalities(self) -> frozenset[OncologyModality]:
        """
        All modalities supported by linked event structure.

        Any linked drug exposure is treated as structural SACT evidence, while
        radiotherapy, surgery, and diagnostic/staging require governed procedure
        concept membership. Events linked to direct child episodes are included.
        """
        modalities: set[OncologyModality] = set()
        for event in self._linked_oncology_events:
            if isinstance(event, OncologyDrugExposure):
                modalities.add(OncologyModality.SACT)
                continue
            if not isinstance(event, OncologyProcedure):
                continue
            if event.is_radiotherapy:
                modalities.add(OncologyModality.RADIOTHERAPY)
            if event.is_surgery:
                modalities.add(OncologyModality.SURGERY)
            if event.is_diagnostic_staging:
                modalities.add(OncologyModality.DIAGNOSTIC_STAGING)
        return frozenset(modalities)

    @cached_property
    def structural_modality(self) -> OncologyModality:
        """
        Highest-priority structural modality, or ``UNKNOWN`` when none apply.

        Priority is radiotherapy, surgery, diagnostic/staging, then SACT.
        """
        return _preferred_modality(self.structural_modalities)

    @cached_property
    def concept_modalities(self) -> frozenset[OncologyModality]:
        """
        All modalities supported by linked procedure and drug concept identity.

        This is intentionally distinct from ``structural_modalities`` so SACT
        disagreements remain visible.
        """
        modalities: set[OncologyModality] = set()
        for event in self._linked_oncology_events:
            if isinstance(event, OncologyDrugExposure):
                if event.is_sact:
                    modalities.add(OncologyModality.SACT)
            elif isinstance(event, OncologyProcedure):
                if event.is_radiotherapy:
                    modalities.add(OncologyModality.RADIOTHERAPY)
                if event.is_surgery:
                    modalities.add(OncologyModality.SURGERY)
                if event.is_diagnostic_staging:
                    modalities.add(OncologyModality.DIAGNOSTIC_STAGING)
        return frozenset(modalities)

    @cached_property
    def concept_modality(self) -> OncologyModality:
        """
        Highest-priority concept modality, or ``UNKNOWN`` when none apply.

        Priority is radiotherapy, surgery, diagnostic/staging, then SACT.
        """
        return _preferred_modality(self.concept_modalities)

    @cached_property
    def child_treatment_episodes_by_modality(
        self,
    ) -> dict[OncologyModality, list[Self]]:
        groups: dict[OncologyModality, list[Self]] = {
            modality: []
            for modality in OncologyModality
        }
        for child in self.child_treatment_episodes:
            groups[child.structural_modality].append(child)
        return groups

    @cached_property
    def child_treatment_episodes_by_concept_modality(
        self,
    ) -> dict[OncologyModality, list[Self]]:
        groups: dict[OncologyModality, list[Self]] = {
            modality: []
            for modality in OncologyModality
        }
        for child in self.child_treatment_episodes:
            groups[child.concept_modality].append(child)
        return groups
