"""Grade clinical severity against published criteria.

Computing that a patient lost 12% of their body weight is a measurement
question.  Deciding what that means is a policy question, and the answer
depends on which published criteria you are applying.  This module keeps
those criteria separate from the measurement code so that the grading
standard in use is always explicit.

``ctcae_weight_loss_grade`` applies CTCAE-style percent-loss bins, using
percent weight change alone.  CTCAE's intervention-based qualifiers are
not inferred from CDM data, so the grade reflects the physiological bins
only.

``martin_weight_loss_grade`` applies the Martin et al. matrix, which
grades percent weight loss against BMI and so distinguishes the same
percentage loss in patients of different body composition.

``critical_weight_loss_grade`` is the combined entry point: it uses
Martin grading where BMI is available and falls back to CTCAE-style
grading where it is not, provided percent change is evaluable::

    from omop_alchemy.toolkit.analytics.adverse_events import (
        critical_weight_loss_grade,
    )

    grade = critical_weight_loss_grade(pct_change=-12.0, bmi=21.4)

Grading functions take computed values, not ORM rows.  Where the values
come from — which readings were admitted, over what window — is decided
in ``omop_alchemy.toolkit.analytics.body_metrics``.
"""

from .weight_loss import (
    MartinWeightLoss,
    critical_weight_loss_grade,
    martin_weight_loss_grade,
    CTCAEWeightLoss, ctcae_weight_loss_grade
)

__all__ = [
    "CTCAEWeightLoss",
    "MartinWeightLoss",
    "critical_weight_loss_grade",
    "ctcae_weight_loss_grade",
    "martin_weight_loss_grade",
]
