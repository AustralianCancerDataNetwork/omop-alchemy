from __future__ import annotations

from dataclasses import dataclass

from omop_alchemy.toolkit.core._semantics import default_semantics_runtime


@dataclass(frozen=True)
class BodySizeMeasurementConcepts:
    weight: int
    height: int


def default_body_size_measurement_concepts() -> BodySizeMeasurementConcepts:
    runtime = default_semantics_runtime()
    measurements = runtime.measurements_numeric.body_size_measurements
    return BodySizeMeasurementConcepts(
        weight=measurements.weight,
        height=measurements.height,
    )
