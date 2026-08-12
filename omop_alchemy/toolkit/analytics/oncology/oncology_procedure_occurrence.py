from __future__ import annotations

from sqlalchemy.ext.hybrid import hybrid_property

from omop_alchemy.cdm.model.clinical.procedure_occurrence import (
    Procedure_OccurrenceView,
)

from .concept_sets import (
    CANCER_INDICATING_SURGERY,
    DIAGNOSTIC_STAGING_PROCEDURES,
    RADIOTHERAPY_PROCEDURES,
    resolve_cancer_indicating_surgery_procedure_concept_ids,
    resolve_diagnostic_staging_procedure_concept_ids,
    resolve_rt_procedure_concept_ids,
)
from .session_guard import require_session


class OncologyProcedure(Procedure_OccurrenceView):
    """
    Oncology-aware procedure occurrence view.

    This maps the standard ``procedure_occurrence`` table and adds governed
    concept-set membership checks used by oncology episode classification.

    Each check is a ``hybrid_property`` with two implementations: on an instance
    it tests membership against the cached expansion, and on the class it emits a
    ``concept_ancestor`` subquery. Both derive from one governed
    ``ConceptGroupSpec``, so they cannot disagree about what the set contains.

    The instance path requires an attached instance -- a detached row cannot
    determine membership, and reporting "no" would silently misclassify.
    """

    @hybrid_property
    def is_radiotherapy(self) -> bool:
        return self.procedure_concept_id in resolve_rt_procedure_concept_ids(
            require_session(self)
        )

    @is_radiotherapy.inplace.expression
    @classmethod
    def _is_radiotherapy_expression(cls):
        return RADIOTHERAPY_PROCEDURES.expression_for(cls.procedure_concept_id)

    @hybrid_property
    def is_surgery(self) -> bool:
        return (
            self.procedure_concept_id
            in resolve_cancer_indicating_surgery_procedure_concept_ids(
                require_session(self)
            )
        )

    @is_surgery.inplace.expression
    @classmethod
    def _is_surgery_expression(cls):
        return CANCER_INDICATING_SURGERY.expression_for(cls.procedure_concept_id)

    @hybrid_property
    def is_diagnostic_staging(self) -> bool:
        return (
            self.procedure_concept_id
            in resolve_diagnostic_staging_procedure_concept_ids(
                require_session(self)
            )
        )

    @is_diagnostic_staging.inplace.expression
    @classmethod
    def _is_diagnostic_staging_expression(cls):
        return DIAGNOSTIC_STAGING_PROCEDURES.expression_for(cls.procedure_concept_id)
