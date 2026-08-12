from __future__ import annotations

from sqlalchemy.ext.hybrid import hybrid_property

from omop_alchemy.cdm.model.clinical.drug_exposure import Drug_ExposureView

from .concept_sets import SACT_DRUGS, resolve_sact_drug_concept_ids
from .session_guard import require_session


class OncologyDrugExposure(Drug_ExposureView):
    """
    Oncology-aware drug exposure view.

    SACT classification is governed by omop-semantics and intentionally kept
    separate from generic drug episode summaries.

    ``is_sact`` is a ``hybrid_property``: on an instance it tests the cached
    expansion, on the class it emits a ``concept_ancestor`` subquery. Both come
    from one governed ``ConceptGroupSpec``, including its exclusions.
    """

    @hybrid_property
    def is_sact(self) -> bool:
        return self.drug_concept_id in resolve_sact_drug_concept_ids(
            require_session(self)
        )

    @is_sact.inplace.expression
    @classmethod
    def _is_sact_expression(cls):
        return SACT_DRUGS.expression_for(cls.drug_concept_id)
