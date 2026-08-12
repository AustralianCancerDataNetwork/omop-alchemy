from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Any, Callable, Hashable, Self, Sequence

from omop_alchemy.cdm.model import Drug_Exposure
from omop_alchemy.toolkit.core._grouping import group_and_summarize
from omop_alchemy.toolkit.episodes.handling import (
    DoseEvaluability,
    DrugExposureSummary,
)

from .oncology_drug_exposure import OncologyDrugExposure


@dataclass(frozen=True)
class SACTDoseSummary(DrugExposureSummary):
    """
    SACT dose summary for one caller-chosen drug grouping.

    This is deliberately a summary interface, not a dose-reduction rule. It
    preserves mixed/missing units as evaluability states for downstream SACT
    policy to interpret.
    """

    evaluability: DoseEvaluability

    @classmethod
    def from_exposures(
        cls,
        exposures: Sequence[Drug_Exposure],
        *,
        group_key: object,
    ) -> Self:
        """Summarize SACT exposures and attach dose evaluability policy.

        Construction is field-based and therefore accepts the base OMOP exposure
        type. Oncology filtering belongs to ``sact_exposures`` before this summary
        boundary; keeping the inherited input type also preserves substitutability.
        """
        return cls(
            **DrugExposureSummary._values_from_exposures(
                exposures,
                group_key=group_key,
            ),
            evaluability=sact_dose_evaluability(exposures),
        )


def sact_dose_evaluability(
    exposures: Sequence[Drug_Exposure],
) -> DoseEvaluability:
    if not exposures:
        return DoseEvaluability(False, "no_sact_exposures")
    if any(exposure.quantity is None for exposure in exposures):
        return DoseEvaluability(False, "missing_quantity")
    units = {
        exposure.dose_unit_source_value
        for exposure in exposures
        if exposure.dose_unit_source_value
    }
    if len(units) > 1:
        return DoseEvaluability(False, "mixed_dose_units")
    return DoseEvaluability(True)


def summarize_sact_exposures_by(
    exposures: Sequence[OncologyDrugExposure],
    key: Callable[[OncologyDrugExposure], Hashable],
) -> list[SACTDoseSummary]:
    return group_and_summarize(exposures, key, SACTDoseSummary.from_exposures)


class OncologySACTDosingMixin:
    """
    SACT dose-summary interface for oncology treatment episodes.

    The composed oncology episode supplies events linked directly to the episode
    and to its direct children. This keeps regimen-level summaries inclusive of
    exposures recorded against child treatment cycles.
    """

    @property
    def _linked_oncology_events(self) -> list[Any]:
        raise NotImplementedError

    @cached_property
    def sact_exposures(self) -> list[OncologyDrugExposure]:
        exposures = [
            event
            for event in self._linked_oncology_events
            if isinstance(event, OncologyDrugExposure) and event.is_sact
        ]
        exposures.sort(key=lambda exposure: exposure.drug_exposure_start_date)
        return exposures

    @cached_property
    def sact_dose_summaries_by_drug_concept(self) -> list[SACTDoseSummary]:
        return summarize_sact_exposures_by(
            self.sact_exposures,
            lambda exposure: exposure.drug_concept_id,
        )

    @cached_property
    def sact_dose_summary(self) -> SACTDoseSummary:
        return SACTDoseSummary.from_exposures(
            self.sact_exposures,
            group_key="all_sact",
        )
