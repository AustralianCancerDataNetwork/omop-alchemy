from __future__ import annotations

from omop_alchemy.toolkit.analytics.adverse_events import (
    CTCAEWeightLoss,
    MartinWeightLoss,
    critical_weight_loss_grade,
    ctcae_weight_loss_grade,
)


def test_martin_weight_loss_reference_cells():
    assert MartinWeightLoss.grade(pct_change=-1, bmi=30) == 0
    assert MartinWeightLoss.grade(pct_change=-20, bmi=18) == 4


def test_martin_bmi_category_boundaries():
    assert [
        MartinWeightLoss._bmi_category_index(bmi)
        for bmi in (30, 26, 23, 21, 18)
    ] == [0, 1, 2, 3, 4]


def test_martin_weight_loss_category_boundaries():
    assert [
        MartinWeightLoss._weight_loss_category_index(loss_pct)
        for loss_pct in (0, 2, 4, 8, 12, 20)
    ] == [0, 0, 1, 2, 3, 4]


def test_ctcae_weight_loss_grade_counts_crossed_thresholds():
    assert CTCAEWeightLoss.grade(-12) == 2
    assert ctcae_weight_loss_grade(-12) == 2


def test_critical_weight_loss_grade_falls_back_to_ctcae_without_bmi():
    assert critical_weight_loss_grade(pct_change=-12, bmi=None) == 2


def test_critical_weight_loss_grade_requires_percent_change():
    assert critical_weight_loss_grade(pct_change=None, bmi=30) is None
