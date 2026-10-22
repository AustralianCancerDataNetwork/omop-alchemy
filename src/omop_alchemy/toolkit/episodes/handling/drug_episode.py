from __future__ import annotations

from functools import cached_property
from typing import Callable, ClassVar, Hashable, Sequence

from omop_alchemy.cdm.model import Drug_Exposure

from .exposure_series import resolve_drug_exposure_series
from .summaries import DrugExposureSummary, summarize_drug_exposures_by


class DrugEpisodeMixin:
    """
    Episode mixin for generic linked-drug retrieval and summary.

    Domain-specific subclasses can supply concept filters, grouping keys, and
    dose validity rules. This base layer only knows how to find and summarise
    drug exposure rows.
    """

    _drug_concept_ids: ClassVar[Sequence[int] | None] = None
    _include_window_drug_exposures: ClassVar[bool] = False

    @cached_property
    def drug_exposures(self) -> list[Drug_Exposure]:
        return resolve_drug_exposure_series(
            self,
            self._drug_concept_ids,
            include_window=self._include_window_drug_exposures,
        )

    def drug_exposure_summaries_by(
        self,
        key: Callable[[Drug_Exposure], Hashable] | None = None,
    ) -> list[DrugExposureSummary]:
        key = key or (lambda exposure: exposure.drug_concept_id)
        return summarize_drug_exposures_by(self.drug_exposures, key)
