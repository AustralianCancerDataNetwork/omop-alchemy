from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from functools import cached_property
from typing import Any, Callable, Hashable, Sequence

from omop_alchemy.toolkit.episodes.handling import (
    DoseEvaluability,
    DrugExposureSummary,
    summarize_drug_exposures,
)

from .oncology_drug_exposure import OncologyDrugExposure


@dataclass(frozen=True)
class SACTDoseSummary:
    """
    SACT dose summary for one caller-chosen drug grouping.

    This is deliberately a summary interface, not a dose-reduction rule. It
    preserves mixed/missing units as evaluability states for downstream SACT
    policy to interpret.
    """

    exposure_summary: DrugExposureSummary
    evaluability: DoseEvaluability

    @property
    def group_key(self) -> object:
        return self.exposure_summary.group_key

    @property
    def n_exposures(self) -> int:
        return self.exposure_summary.n_exposures

    @property
    def first_start_date(self) -> date | None:
        return self.exposure_summary.first_start_date

    @property
    def last_start_date(self) -> date | None:
        return self.exposure_summary.last_start_date

    @property
    def total_quantity(self) -> float | None:
        return self.exposure_summary.total_quantity

    @property
    def dose_unit_source_values(self) -> frozenset[str]:
        return self.exposure_summary.dose_unit_source_values

    @property
    def drug_concept_ids(self) -> frozenset[int]:
        return self.exposure_summary.drug_concept_ids


def sact_dose_evaluability(
    exposures: Sequence[OncologyDrugExposure],
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


def summarize_sact_exposures(
    exposures: Sequence[OncologyDrugExposure],
    *,
    group_key: object,
) -> SACTDoseSummary:
    return SACTDoseSummary(
        exposure_summary=summarize_drug_exposures(exposures, group_key=group_key),
        evaluability=sact_dose_evaluability(exposures),
    )


def summarize_sact_exposures_by(
    exposures: Sequence[OncologyDrugExposure],
    key: Callable[[OncologyDrugExposure], Hashable],
) -> list[SACTDoseSummary]:
    grouped: dict[Hashable, list[OncologyDrugExposure]] = defaultdict(list)
    for exposure in exposures:
        grouped[key(exposure)].append(exposure)
    return [
        summarize_sact_exposures(rows, group_key=group_key)
        for group_key, rows in grouped.items()
    ]


class OncologySACTDosingMixin:
    """
    SACT dose-summary interface for oncology treatment episodes.
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
        return summarize_sact_exposures(
            self.sact_exposures,
            group_key="all_sact",
        )
