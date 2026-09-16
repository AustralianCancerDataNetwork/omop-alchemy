"""Behavioural coverage for the lightweight clinical event timeline."""

from __future__ import annotations

from datetime import date, datetime
import json

import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

from omop_alchemy.cdm.base import ModifierFieldConcepts
from omop_alchemy.cdm.model.clinical import MeasurementView, ObservationView
from omop_alchemy.toolkit.core.events import ClinicalEventRow
from omop_alchemy.toolkit.core.timeline import (
    ClinicalEvent,
    Condition_Event,
    Drug_Exposure_Event,
    Measurement_Event,
    Observation_Event,
    Person_Timeline,
)


def test_observation_event_implements_the_timeline_contract():
    event = Observation_Event(
        observation_id=7,
        person_id=101,
        observation_concept_id=900_001,
        observation_date=date(2026, 1, 20),
        observation_datetime=datetime(2026, 1, 20, 9, 30),
        observation_type_concept_id=32817,
        value_as_string="family history",
    )

    assert isinstance(event, ClinicalEventRow)
    assert event.event_id == 7
    assert event.event_source_table == "observation"
    assert event.event_field_concept_id == ModifierFieldConcepts.OBSERVATION
    assert event.event_concept_id == 900_001
    assert event.event_date == date(2026, 1, 20)
    assert event.event_time.start == datetime(2026, 1, 20, 9, 30)
    assert event.event_time.end is None
    assert event.event_value().type == "string"
    assert event.event_value().value == "family history"

    payload = json.loads(event.to_json())
    assert payload["event_id"] == 7
    assert payload["event_source_table"] == "observation"
    assert payload["event_concept_id"] == 900_001
    assert "concept_id" not in payload


def test_observation_event_is_part_of_person_timeline_and_compiles():
    assert Observation_Event in Person_Timeline.EVENT_TABLES

    statement = sa.select(Observation_Event).where(Observation_Event.person_id == 101)
    compiled = str(statement.compile(dialect=sqlite.dialect()))

    assert "observation" in compiled
    assert "person_id" in compiled


def test_drug_exposure_quantity_is_a_numeric_timeline_value():
    event = Drug_Exposure_Event(
        drug_exposure_id=8,
        person_id=101,
        drug_concept_id=900_002,
        drug_exposure_start_date=date(2026, 1, 21),
        drug_type_concept_id=32817,
        quantity=12.5,
    )

    assert event.event_value().type == "numeric"
    assert event.event_value().value == 12.5
    assert event.to_dict()["value"] == {"type": "numeric", "value": 12.5}


def test_measurement_event_only_declares_measurement_value_columns():
    assert Measurement_Event._mapping.value_fields == [
        "value_as_concept_id",
        "value_as_number",
    ]


def test_measurement_and_observation_views_expose_schema_backed_unit_context():
    measurement_relationships = MeasurementView.__mapper__.relationships
    observation_relationships = ObservationView.__mapper__.relationships

    assert "unit_concept" in measurement_relationships
    assert "unit_source_concept" in measurement_relationships
    assert "unit_concept" in observation_relationships
    assert "unit_source_concept" not in observation_relationships


def test_all_timeline_events_use_clinical_event_behaviour():
    assert Condition_Event.to_json is ClinicalEvent.to_json
    assert Drug_Exposure_Event.to_json is ClinicalEvent.to_json
    assert Observation_Event.to_json is ClinicalEvent.to_json
