from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import cached_property
from typing import ClassVar, Optional, TypedDict

from .calculators import BodyMetricRules
from .measurement_series import (
    MeasurementReading,
    resolve_measurement_series,
    resolve_person_measurement_series,
)


@dataclass(frozen=True)
class WeightChange:
    """
    Result of a windowed weight-change computation.

    ``pct_change`` is None whenever the change is not evaluable. Keeping that
    explicit prevents callers from folding "unknown" into "no loss".
    """

    pct_change: Optional[float]
    evaluable: bool
    reference: Optional[MeasurementReading] = None

    @classmethod
    def not_evaluable(cls) -> "WeightChange":
        return cls(pct_change=None, evaluable=False)


@dataclass(frozen=True)
class WeightTrajectoryPoint:
    day_offset: int
    pct_change: float
    date: date
    weight_kg: float


class WeightTrajectorySummary(TypedDict):
    """Stable dict-shaped result returned by ``weight_trajectory_summary``."""

    episode_id: int
    person_id: int
    n_weight_readings: int
    baseline_weight_kg: float | None
    baseline_weight_date: date | None
    latest_weight_kg: float | None
    latest_weight_date: date | None
    height_m: float | None
    baseline_bmi: float | None
    baseline_bsa_mosteller_m2: float | None
    pct_change_from_baseline: float | None
    pct_change_from_baseline_evaluable: bool
    pct_change_3mo: float | None
    pct_change_3mo_evaluable: bool
    pct_change_6mo: float | None
    pct_change_6mo_evaluable: bool
    sustained_5pct_loss: bool | None


def normalize_weight_readings(
    readings: list[MeasurementReading],
    rules: BodyMetricRules,
) -> list[MeasurementReading]:
    normalized: list[MeasurementReading] = []
    for reading in readings:
        kg = rules.normalize_weight_kg(reading.value, reading.unit_concept_id)
        if kg is None:
            continue
        normalized.append(replace(reading, value=kg, unit_concept_id=rules.units.kg))
    return normalized


def normalize_height_readings(
    readings: list[MeasurementReading],
    rules: BodyMetricRules,
) -> list[MeasurementReading]:
    normalized: list[MeasurementReading] = []
    for reading in readings:
        cm = rules.normalize_height_cm(reading.value, reading.unit_concept_id)
        if cm is None:
            continue
        normalized.append(replace(reading, value=cm, unit_concept_id=rules.units.cm))
    return normalized


class WeightTrajectoryMixin:
    """
    Episode mixin exposing normalized weight, height, BMI, and trajectories.

    Subclasses may set ``_body_metric_rules`` to avoid loading the default
    omop-semantics-backed concept IDs.
    """

    _body_metric_rules: ClassVar[BodyMetricRules | None] = None
    _exclude_modifier_measurements: ClassVar[bool] = True
    episode_id: ClassVar[int]
    person_id: ClassVar[int]

    @classmethod
    def body_metric_rules(cls) -> BodyMetricRules:
        return cls._body_metric_rules or BodyMetricRules.default()

    @cached_property
    def _raw_weight_series(self) -> list[MeasurementReading]:
        rules = self.body_metric_rules()
        return resolve_measurement_series(
            self,
            (rules.weight_concept_id,),
            exclude_modifiers=self._exclude_modifier_measurements,
        )

    @cached_property
    def _raw_height_series(self) -> list[MeasurementReading]:
        rules = self.body_metric_rules()
        return resolve_person_measurement_series(
            self,
            (rules.height_concept_id,),
            exclude_modifiers=self._exclude_modifier_measurements,
        )

    @cached_property
    def weight_readings(self) -> list[MeasurementReading]:
        """Weight readings normalized to kg."""
        return normalize_weight_readings(self._raw_weight_series, self.body_metric_rules())

    @cached_property
    def height_readings_cm(self) -> list[MeasurementReading]:
        """Height readings normalized to cm and resolved without an episode date window."""
        return normalize_height_readings(self._raw_height_series, self.body_metric_rules())

    @property
    def height_m(self) -> Optional[float]:
        readings = self.height_readings_cm
        if not readings or readings[0].value is None:
            return None
        return readings[0].value / 100.0

    @property
    def baseline_weight(self) -> Optional[MeasurementReading]:
        return self.weight_readings[0] if self.weight_readings else None

    @property
    def latest_weight(self) -> Optional[MeasurementReading]:
        return self.weight_readings[-1] if self.weight_readings else None

    @property
    def baseline_bmi(self) -> Optional[float]:
        baseline = self.baseline_weight
        return self.body_metric_rules().bmi(
            baseline.value if baseline else None,
            self.height_m,
        )

    @property
    def baseline_bsa_mosteller_m2(self) -> Optional[float]:
        baseline = self.baseline_weight
        height = self.height_readings_cm[0] if self.height_readings_cm else None
        return self.body_metric_rules().bsa_mosteller_m2(
            baseline.value if baseline else None,
            height.value if height else None,
        )

    def pct_change_from_baseline(
        self,
        as_of: Optional[MeasurementReading] = None,
    ) -> WeightChange:
        baseline = self.baseline_weight
        target = as_of or self.latest_weight
        if (
            baseline is None
            or target is None
            or baseline.measurement_id == target.measurement_id
            or baseline.value is None
            or baseline.value <= 0
            or target.value is None
        ):
            return WeightChange.not_evaluable()
        pct = 100.0 * (target.value - baseline.value) / baseline.value
        return WeightChange(pct_change=pct, evaluable=True, reference=baseline)

    def pct_change_over(self, days: int) -> WeightChange:
        readings = self.weight_readings
        if len(readings) < 2:
            return WeightChange.not_evaluable()
        latest = readings[-1]
        window_start = latest.date - timedelta(days=days)
        candidates = [r for r in readings if r.date >= window_start]
        earliest_in_window = candidates[0]
        if (
            latest.value is None
            or earliest_in_window.value is None
            or earliest_in_window.value <= 0
            or earliest_in_window.measurement_id == latest.measurement_id
        ):
            return WeightChange.not_evaluable()
        pct = 100.0 * (latest.value - earliest_in_window.value) / earliest_in_window.value
        return WeightChange(pct_change=pct, evaluable=True, reference=earliest_in_window)

    def pct_change_trajectory(self) -> list[WeightTrajectoryPoint]:
        baseline = self.baseline_weight
        if baseline is None or baseline.value is None or baseline.value <= 0:
            return []
        points: list[WeightTrajectoryPoint] = []
        for reading in self.weight_readings:
            if reading.value is None:
                continue
            day_offset = (reading.date - baseline.date).days
            pct = 100.0 * (reading.value - baseline.value) / baseline.value
            points.append(
                WeightTrajectoryPoint(
                    day_offset=day_offset,
                    pct_change=pct,
                    date=reading.date,
                    weight_kg=reading.value,
                )
            )
        return points

    def sustained_loss(
        self,
        threshold_pct: float = 5.0,
        min_consecutive: int = 2,
    ) -> Optional[bool]:
        baseline = self.baseline_weight
        # the baseline is the first reading, so it cannot count towards a loss against itself
        post_baseline = self.weight_readings[1:]
        if (
            baseline is None
            or baseline.value is None
            or baseline.value <= 0
            or len(post_baseline) < min_consecutive
        ):
            return None
        tail = post_baseline[-min_consecutive:]
        if any(r.value is None for r in tail):
            return None
        return all(
            r.value is not None
            and (100.0 * (baseline.value - r.value) / baseline.value) >= threshold_pct
            for r in tail
        )

    def weight_trajectory_summary(self) -> WeightTrajectorySummary:
        baseline = self.baseline_weight
        latest = self.latest_weight
        change_from_baseline = self.pct_change_from_baseline()
        change_3mo = self.pct_change_over(90)
        change_6mo = self.pct_change_over(180)
        return {
            "episode_id": self.episode_id,
            "person_id": self.person_id,
            "n_weight_readings": len(self.weight_readings),
            "baseline_weight_kg": baseline.value if baseline else None,
            "baseline_weight_date": baseline.date if baseline else None,
            "latest_weight_kg": latest.value if latest else None,
            "latest_weight_date": latest.date if latest else None,
            "height_m": self.height_m,
            "baseline_bmi": self.baseline_bmi,
            "baseline_bsa_mosteller_m2": self.baseline_bsa_mosteller_m2,
            "pct_change_from_baseline": change_from_baseline.pct_change,
            "pct_change_from_baseline_evaluable": change_from_baseline.evaluable,
            "pct_change_3mo": change_3mo.pct_change,
            "pct_change_3mo_evaluable": change_3mo.evaluable,
            "pct_change_6mo": change_6mo.pct_change,
            "pct_change_6mo_evaluable": change_6mo.evaluable,
            "sustained_5pct_loss": self.sustained_loss(),
        }
