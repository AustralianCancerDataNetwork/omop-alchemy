from __future__ import annotations

from datetime import date

import pytest

from omop_alchemy.toolkit.analytics.body_metrics import (
    BodyMetricRules,
    BodySizeMeasurementConcepts,
    MeasurementReading,
    WeightTrajectoryMixin,
    WeightTrajectorySummary,
)
from omop_alchemy.toolkit.analytics.oncology import (
    CriticalWeightLossSummary,
    OncologyCriticalWeightLossMixin,
)
from omop_alchemy.toolkit.core.units import BodySizeUnitConcepts, BodyUnitConversionRules


def _body_metric_rules() -> BodyMetricRules:
    return BodyMetricRules(
        measurements=BodySizeMeasurementConcepts(weight=1, height=2),
        unit_rules=BodyUnitConversionRules(
            units=BodySizeUnitConcepts(kg=10, lb=11, cm=12, inch=13, m2=14),
        ),
    )


def test_body_metric_rules_normalize_weight_kg():
    rules = _body_metric_rules()

    assert rules.normalize_weight_kg(70, rules.units.kg) == 70
    assert rules.normalize_weight_kg(150, rules.units.lb) == pytest.approx(68.0388555)
    assert rules.normalize_weight_kg(70, 999) is None
    assert rules.normalize_weight_kg(None, rules.units.kg) is None
    assert rules.normalize_weight_kg(70, None) is None


def test_body_metric_rules_normalize_height_cm():
    rules = _body_metric_rules()

    assert rules.normalize_height_cm(170, rules.units.cm) == 170
    assert rules.normalize_height_cm(67, rules.units.inch) == pytest.approx(170.18)
    assert rules.normalize_height_cm(170, 999) is None
    assert rules.normalize_height_cm(None, rules.units.cm) is None
    assert rules.normalize_height_cm(170, None) is None


def test_body_metric_rules_bmi():
    assert BodyMetricRules.bmi(70, 1.7) == pytest.approx(24.22, abs=0.01)
    assert BodyMetricRules.bmi(None, 1.7) is None
    assert BodyMetricRules.bmi(70, None) is None
    assert BodyMetricRules.bmi(0, 1.7) is None


def test_body_metric_rules_bsa_mosteller_m2():
    assert BodyMetricRules.bsa_mosteller_m2(70, 170) == pytest.approx(1.818, abs=0.001)


class WeightTrajectoryStub(WeightTrajectoryMixin):
    def __init__(self, readings: list[MeasurementReading]) -> None:
        self.episode_id = 1
        self.person_id = 1
        self._readings = readings

    @property
    def weight_readings(self) -> list[MeasurementReading]:
        return self._readings

    @property
    def height_m(self) -> float | None:
        return None

    @property
    def baseline_bmi(self) -> float | None:
        return None

    @property
    def baseline_bsa_mosteller_m2(self) -> float | None:
        return None


class CriticalWeightTrajectoryStub(OncologyCriticalWeightLossMixin):
    def __init__(self, readings: list[MeasurementReading]) -> None:
        self.episode_id = 1
        self.person_id = 1
        self._readings = readings

    @property
    def weight_readings(self) -> list[MeasurementReading]:
        return self._readings

    @property
    def height_m(self) -> float | None:
        return 1.8

    @property
    def baseline_bmi(self) -> float | None:
        return 20.0

    @property
    def baseline_bsa_mosteller_m2(self) -> float | None:
        return None


def _weight_reading(
    measurement_id: int,
    value: float,
    reading_date: date = date(2020, 1, 1),
) -> MeasurementReading:
    return MeasurementReading(
        measurement_id=measurement_id,
        date=reading_date,
        value=value,
        unit_concept_id=10,
        concept_id=1,
        source="explicit",
    )


def test_weight_change_single_reading_is_not_evaluable():
    episode = WeightTrajectoryStub([_weight_reading(1, 100)])

    change = episode.pct_change_from_baseline()

    assert not change.evaluable
    assert change.pct_change is None


def test_weight_change_same_measurement_id_is_not_evaluable():
    baseline = _weight_reading(1, 100)
    same_measurement = _weight_reading(1, 95, date(2020, 1, 5))
    episode = WeightTrajectoryStub([baseline])

    change = episode.pct_change_from_baseline(as_of=same_measurement)

    assert not change.evaluable
    assert change.pct_change is None


def test_weight_change_near_zero_change_remains_evaluable():
    episode = WeightTrajectoryStub(
        [
            _weight_reading(1, 100, date(2020, 1, 1)),
            _weight_reading(2, 100.01, date(2020, 1, 5)),
        ]
    )

    change = episode.pct_change_from_baseline()

    assert change.evaluable
    assert change.pct_change == pytest.approx(0.01)


def test_sustained_loss_ignores_the_baseline_reading():
    # min_consecutive readings *after* the baseline, all past the threshold
    episode = WeightTrajectoryStub(
        [
            _weight_reading(1, 100, date(2020, 1, 1)),
            _weight_reading(2, 93, date(2020, 2, 1)),
            _weight_reading(3, 91, date(2020, 3, 1)),
        ]
    )

    assert episode.sustained_loss() is True


def test_sustained_loss_needs_enough_readings_after_the_baseline():
    episode = WeightTrajectoryStub(
        [
            _weight_reading(1, 100, date(2020, 1, 1)),
            _weight_reading(2, 93, date(2020, 2, 1)),
        ]
    )

    assert episode.sustained_loss(min_consecutive=2) is None
    assert episode.sustained_loss(min_consecutive=1) is True


def test_sustained_loss_false_when_a_later_reading_recovers():
    episode = WeightTrajectoryStub(
        [
            _weight_reading(1, 100, date(2020, 1, 1)),
            _weight_reading(2, 93, date(2020, 2, 1)),
            _weight_reading(3, 99, date(2020, 3, 1)),
        ]
    )

    assert episode.sustained_loss() is False


def test_weight_summary_contract_remains_dict_shaped():
    episode = WeightTrajectoryStub(
        [
            _weight_reading(1, 100, date(2020, 1, 1)),
            _weight_reading(2, 90, date(2020, 6, 1)),
        ]
    )

    summary: WeightTrajectorySummary = episode.weight_trajectory_summary()

    assert isinstance(summary, dict)
    assert summary["episode_id"] == 1
    assert summary["pct_change_from_baseline"] == pytest.approx(-10.0)


def test_critical_weight_summary_extends_weight_contract_without_mutation():
    episode = CriticalWeightTrajectoryStub(
        [
            _weight_reading(1, 100, date(2020, 1, 1)),
            _weight_reading(2, 90, date(2020, 6, 1)),
        ]
    )

    weight_summary = episode.weight_trajectory_summary()
    summary: CriticalWeightLossSummary = episode.critical_weight_loss_summary()

    assert isinstance(summary, dict)
    assert "ctcae_weight_loss_grade" not in weight_summary
    assert summary["ctcae_weight_loss_grade"] is not None
    assert summary["martin_weight_loss_grade"] is not None
    assert summary["critical_weight_loss_grade"] is not None
