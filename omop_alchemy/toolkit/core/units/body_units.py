from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from omop_alchemy.toolkit.core._semantics import default_semantics_runtime

LB_US_TO_KG = 0.45359237
INCH_TO_CM = 2.54


@dataclass(frozen=True)
class BodySizeUnitConcepts:
    """
    The unit concepts anthropometric conversion recognises.

    Kilograms, pounds, centimetres, inches, and square metres mean the same
    thing in every clinical domain, so the mapping from concept ID to unit
    lives here rather than with any one domain's measurement logic.
    """

    kg: int
    lb: int
    cm: int
    inch: int
    m2: int


def default_body_size_unit_concepts() -> BodySizeUnitConcepts:
    runtime = default_semantics_runtime()
    units = runtime.measurements_numeric.body_size_units
    return BodySizeUnitConcepts(
        kg=units.kg,
        lb=units.lb,
        cm=units.cm,
        inch=units.inch,
        m2=units.m2,
    )


@dataclass(frozen=True)
class BodyUnitConversionRules:
    """
    Unit normalisation for anthropometric measurements.

    Conversion is driven by the unit concept recorded on each row. A value
    whose unit concept is not in ``units`` converts to None rather than being
    passed through, so a reading in an unrecognised unit cannot reach a
    calculation disguised as a valid one.

    Deployments that record body size against non-standard unit concepts can
    supply their own ``units`` instead of the governed defaults.
    """

    units: BodySizeUnitConcepts

    @classmethod
    def default(cls) -> "BodyUnitConversionRules":
        return default_body_unit_conversion_rules()

    @property
    def weight_unit_conversion_to_kg(self) -> dict[int, float]:
        return {
            self.units.kg: 1.0,
            self.units.lb: LB_US_TO_KG,
        }

    @property
    def height_unit_conversion_to_cm(self) -> dict[int, float]:
        return {
            self.units.cm: 1.0,
            self.units.inch: INCH_TO_CM,
        }

    def normalize_weight_kg(
        self,
        value: Optional[float],
        unit_concept_id: Optional[int],
    ) -> Optional[float]:
        if value is None or unit_concept_id is None:
            return None
        factor = self.weight_unit_conversion_to_kg.get(unit_concept_id)
        return value * factor if factor is not None else None

    def normalize_height_cm(
        self,
        value: Optional[float],
        unit_concept_id: Optional[int],
    ) -> Optional[float]:
        if value is None or unit_concept_id is None:
            return None
        factor = self.height_unit_conversion_to_cm.get(unit_concept_id)
        return value * factor if factor is not None else None


@lru_cache(maxsize=1)
def default_body_unit_conversion_rules() -> BodyUnitConversionRules:
    return BodyUnitConversionRules(units=default_body_size_unit_concepts())
