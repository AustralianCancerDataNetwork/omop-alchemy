from __future__ import annotations

from functools import cached_property

from omop_alchemy.toolkit.analytics.adverse_events import (
    critical_weight_loss_grade,
    ctcae_weight_loss_grade,
    martin_weight_loss_grade,
)
from omop_alchemy.toolkit.analytics.body_metrics import (
    WeightTrajectoryMixin,
    WeightTrajectorySummary,
)


class CriticalWeightLossSummary(WeightTrajectorySummary):
    """Weight trajectory summary extended with oncology loss grades."""

    ctcae_weight_loss_grade: int | None
    martin_weight_loss_grade: int | None
    critical_weight_loss_grade: int | None


class OncologyCriticalWeightLossMixin(WeightTrajectoryMixin):
    """
    Critical-weight-loss support for oncology episodes.

    Body measurements stay in ``body_metrics`` and grading policy stays in
    ``adverse_events``. This mixin only composes those reusable pieces onto the
    oncology episode surface.
    """

    @cached_property
    def ctcae_weight_loss_grade(self) -> int | None:
        return ctcae_weight_loss_grade(
            self.pct_change_from_baseline().pct_change,
        )

    @cached_property
    def martin_weight_loss_grade(self) -> int | None:
        return martin_weight_loss_grade(
            self.pct_change_from_baseline().pct_change,
            self.baseline_bmi,
        )

    @cached_property
    def critical_weight_loss_grade(self) -> int | None:
        return critical_weight_loss_grade(
            self.pct_change_from_baseline().pct_change,
            self.baseline_bmi,
        )

    def critical_weight_loss_summary(self) -> CriticalWeightLossSummary:
        return {
            **self.weight_trajectory_summary(),
            "ctcae_weight_loss_grade": self.ctcae_weight_loss_grade,
            "martin_weight_loss_grade": self.martin_weight_loss_grade,
            "critical_weight_loss_grade": self.critical_weight_loss_grade,
        }
