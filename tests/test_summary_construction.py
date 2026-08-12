from __future__ import annotations

from datetime import date

from omop_alchemy.cdm.model import Drug_Exposure, Measurement
from omop_alchemy.toolkit.analytics.body_metrics import MeasurementReading
from omop_alchemy.toolkit.analytics.oncology import (
    OncologyDrugExposure,
    OncologyProcedure,
    RTDoseSummary,
    SACTDoseSummary,
    summarize_rt_procedures_by,
    summarize_sact_exposures_by,
)
from omop_alchemy.toolkit.episodes.handling import (
    DrugExposureSummary,
    summarize_drug_exposures_by,
)


def _drug_exposure(
    exposure_id: int,
    *,
    concept_id: int,
    start_date: date,
    quantity: float | None,
    unit: str | None = "mg",
) -> OncologyDrugExposure:
    return OncologyDrugExposure(
        drug_exposure_id=exposure_id,
        drug_exposure_start_date=start_date,
        drug_exposure_end_date=start_date,
        drug_concept_id=concept_id,
        drug_type_concept_id=1,
        quantity=quantity,
        dose_unit_source_value=unit,
    )


def _rt_procedure(
    procedure_id: int,
    *,
    concept_id: int,
    procedure_date: date,
    modifier_id: int | None,
    quantity: int | None,
) -> OncologyProcedure:
    return OncologyProcedure(
        procedure_occurrence_id=procedure_id,
        procedure_concept_id=concept_id,
        procedure_date=procedure_date,
        procedure_type_concept_id=1,
        modifier_concept_id=modifier_id,
        quantity=quantity,
    )


def test_measurement_reading_is_constructed_by_its_classmethod() -> None:
    measurement = Measurement(
        measurement_id=7,
        person_id=1,
        measurement_concept_id=100,
        measurement_date=date(2024, 2, 3),
        measurement_type_concept_id=1,
        value_as_number=72.5,
        unit_concept_id=9529,
    )

    reading = MeasurementReading.from_measurement(measurement, source="window")

    assert reading == MeasurementReading(
        measurement_id=7,
        date=date(2024, 2, 3),
        value=72.5,
        unit_concept_id=9529,
        concept_id=100,
        source="window",
    )


def test_drug_exposure_summary_classmethod_and_grouping_preserve_behavior() -> None:
    exposures = [
        _drug_exposure(
            1,
            concept_id=20,
            start_date=date(2024, 1, 2),
            quantity=2.0,
        ),
        _drug_exposure(
            2,
            concept_id=10,
            start_date=date(2024, 1, 3),
            quantity=4.0,
        ),
        _drug_exposure(
            3,
            concept_id=20,
            start_date=date(2024, 1, 4),
            quantity=3.0,
        ),
    ]

    summary = DrugExposureSummary.from_exposures(exposures, group_key="all")
    grouped = summarize_drug_exposures_by(
        exposures,
        lambda exposure: exposure.drug_concept_id,
    )

    assert summary.n_exposures == 3
    assert summary.total_quantity == 9.0
    assert summary.first_start_date == date(2024, 1, 2)
    assert summary.last_start_date == date(2024, 1, 4)
    assert [item.group_key for item in grouped] == [20, 10]
    assert [item.total_quantity for item in grouped] == [5.0, 4.0]


def test_rt_summary_classmethod_and_grouping_preserve_first_seen_order() -> None:
    procedures = [
        _rt_procedure(
            1,
            concept_id=100,
            procedure_date=date(2024, 3, 1),
            modifier_id=30,
            quantity=2,
        ),
        _rt_procedure(
            2,
            concept_id=200,
            procedure_date=date(2024, 3, 2),
            modifier_id=10,
            quantity=1,
        ),
        _rt_procedure(
            3,
            concept_id=100,
            procedure_date=date(2024, 3, 3),
            modifier_id=30,
            quantity=3,
        ),
    ]

    summary = RTDoseSummary.from_procedures(procedures, group_key="all")
    grouped = summarize_rt_procedures_by(
        procedures,
        lambda procedure: procedure.modifier_concept_id,
    )

    assert summary.total_quantity == 6
    assert summary.procedure_concept_ids == frozenset({100, 200})
    assert summary.evaluability.evaluable
    assert [item.group_key for item in grouped] == [30, 10]
    assert [item.n_procedures for item in grouped] == [2, 1]


def test_sact_summary_inherits_generic_fields_and_adds_evaluability() -> None:
    exposures = [
        _drug_exposure(
            1,
            concept_id=20,
            start_date=date(2024, 4, 1),
            quantity=2.0,
            unit="mg",
        ),
        _drug_exposure(
            2,
            concept_id=10,
            start_date=date(2024, 4, 2),
            quantity=3.0,
            unit="mL",
        ),
    ]

    summary = SACTDoseSummary.from_exposures(exposures, group_key="all")
    grouped = summarize_sact_exposures_by(
        exposures,
        lambda exposure: exposure.drug_concept_id,
    )

    assert isinstance(summary, DrugExposureSummary)
    assert not hasattr(summary, "exposure_summary")
    assert summary.total_quantity == 5.0
    assert not summary.evaluability.evaluable
    assert summary.evaluability.reason == "mixed_dose_units"
    assert [item.group_key for item in grouped] == [20, 10]
    assert all(isinstance(item, DrugExposureSummary) for item in grouped)


def test_sact_constructor_accepts_the_generic_exposure_contract() -> None:
    exposure = Drug_Exposure(
        drug_exposure_id=1,
        drug_exposure_start_date=date(2024, 5, 1),
        drug_exposure_end_date=date(2024, 5, 1),
        drug_concept_id=10,
        drug_type_concept_id=1,
        quantity=1.0,
        dose_unit_source_value="mg",
    )

    summary = SACTDoseSummary.from_exposures([exposure], group_key=10)

    assert summary.evaluability.evaluable
    assert summary.drug_concept_ids == frozenset({10})
