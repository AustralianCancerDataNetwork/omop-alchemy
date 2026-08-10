from __future__ import annotations

from enum import StrEnum
from functools import cached_property
from typing import Self, cast

import sqlalchemy.orm as so
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import object_session

from omop_alchemy.cdm.model.structural import EpisodeView
from omop_alchemy.toolkit.episodes.handling import DrugEpisodeMixin

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


class OncologyModality(StrEnum):
    SACT = "sact"
    RADIOTHERAPY = "radiotherapy"
    SURGERY = "surgery"
    DIAGNOSTIC_STAGING = "diagnostic_staging"
    UNKNOWN = "unknown"


class OncologyEpisode(
    OncologyCriticalWeightLossMixin,
    OncologySACTDosingMixin,
    OncologyRTDosingMixin,
    DrugEpisodeMixin,
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
        resolved: list[object] = list(self.events)
        for child in cast(list[Self], self.children):
            resolved.extend(child.events)
        return resolved

    @cached_property
    def structural_modality(self) -> OncologyModality:
        """
        Classify modality from linked event structure.

        Any linked drug exposure is treated as structural SACT evidence, while
        radiotherapy, surgery, and diagnostic/staging require governed procedure
        concept membership.
        """
        has_drug_exposure = False
        for event in self._linked_oncology_events:
            if isinstance(event, OncologyDrugExposure):
                has_drug_exposure = True
                continue
            if not isinstance(event, OncologyProcedure):
                continue
            if event.is_radiotherapy:
                return OncologyModality.RADIOTHERAPY
            if event.is_surgery:
                return OncologyModality.SURGERY
            if event.is_diagnostic_staging:
                return OncologyModality.DIAGNOSTIC_STAGING
        return OncologyModality.SACT if has_drug_exposure else OncologyModality.UNKNOWN

    @cached_property
    def concept_modality(self) -> OncologyModality:
        """
        Classify modality from linked procedure/drug concept identity.

        This is intentionally distinct from ``structural_modality`` so SACT
        disagreements remain visible.
        """
        for event in self._linked_oncology_events:
            if isinstance(event, OncologyDrugExposure):
                if event.is_sact:
                    return OncologyModality.SACT
            elif isinstance(event, OncologyProcedure):
                if event.is_radiotherapy:
                    return OncologyModality.RADIOTHERAPY
                if event.is_surgery:
                    return OncologyModality.SURGERY
                if event.is_diagnostic_staging:
                    return OncologyModality.DIAGNOSTIC_STAGING
        return OncologyModality.UNKNOWN

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
