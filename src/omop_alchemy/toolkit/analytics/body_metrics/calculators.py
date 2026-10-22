from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from omop_alchemy.toolkit.core.units import (
    BodySizeUnitConcepts,
    BodyUnitConversionRules,
    default_body_unit_conversion_rules,
)

from .concept_sets import (
    BodySizeMeasurementConcepts,
    default_body_size_measurement_concepts,
)


@dataclass(frozen=True)
class BodyMetricRules:
    """
    Anthropometric unit normalisation and body-size formula rules.
    """

    measurements: BodySizeMeasurementConcepts
    unit_rules: BodyUnitConversionRules

    @classmethod
    def default(cls) -> "BodyMetricRules":
        return default_body_metric_rules()

    @property
    def units(self) -> BodySizeUnitConcepts:
        return self.unit_rules.units

    @property
    def weight_concept_id(self) -> int:
        return self.measurements.weight

    @property
    def height_concept_id(self) -> int:
        return self.measurements.height

    def normalize_weight_kg(
        self,
        value: Optional[float],
        unit_concept_id: Optional[int],
    ) -> Optional[float]:
        return self.unit_rules.normalize_weight_kg(value, unit_concept_id)

    def normalize_height_cm(
        self,
        value: Optional[float],
        unit_concept_id: Optional[int],
    ) -> Optional[float]:
        return self.unit_rules.normalize_height_cm(value, unit_concept_id)

    @staticmethod
    def bmi(weight_kg: Optional[float], height_m: Optional[float]) -> Optional[float]:
        if not weight_kg or not height_m:
            return None
        return weight_kg / (height_m**2)

    @staticmethod
    def bsa_mosteller_m2(
        weight_kg: Optional[float],
        height_cm: Optional[float],
    ) -> Optional[float]:
        if not weight_kg or not height_cm:
            return None
        return math.sqrt((height_cm * weight_kg) / 3600.0)


@lru_cache(maxsize=1)
def default_body_metric_rules() -> BodyMetricRules:
    return BodyMetricRules(
        measurements=default_body_size_measurement_concepts(),
        unit_rules=default_body_unit_conversion_rules(),
    )
