from __future__ import annotations

from typing import ClassVar, Optional


class CTCAEWeightLoss:
    """
    CTCAE-style weight-loss severity from percent weight change only.

    This implements physiological percent-loss bins.

    CTCAE intervention qualifiers such as hospitalisation, tube feeding, or TPN
    are not inferred here.
    """

    # NCI CTCAE v5.0, "Weight loss": grade 1 is 5 to <10% loss from baseline,
    # grade 2 is 10 to <20%, grade 3 is >=20%. CTCAE defines no grade 4 or 5.
    THRESHOLDS: ClassVar[tuple[float, ...]] = (5.0, 10.0, 20.0)

    @classmethod
    def grade(cls, pct_change: Optional[float]) -> Optional[int]:
        if pct_change is None:
            return None
        loss_pct = max(0.0, -pct_change)
        return sum(1 for threshold in cls.THRESHOLDS if loss_pct >= threshold)


def ctcae_weight_loss_grade(pct_change: Optional[float]) -> Optional[int]:
    return CTCAEWeightLoss.grade(pct_change)
