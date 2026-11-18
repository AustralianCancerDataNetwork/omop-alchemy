"""Tests for the relaxed ValueMixin: no table mandates a populated value column.

OMOP CDM v5.4 does not require a captured result value on every row for
Observation, Measurement, or Metadata, so none of them enforce a "some value
must be present" constraint.
"""

from datetime import date

import pytest

from omop_alchemy.cdm.model.clinical.measurement import Measurement
from omop_alchemy.cdm.model.clinical.observation import Observation
from omop_alchemy.cdm.model.metadata.metadata import Metadata

from .conftest import _concept_id

pytestmark = pytest.mark.sqlite


@pytest.fixture
def type_concept_id(session):
    return _concept_id(session, domain_id="Type Concept")


@pytest.fixture
def some_concept_id(session):
    return _concept_id(session, domain_id="Condition")


def _make_observation(*, observation_id, type_concept_id, some_concept_id, **value_kwargs):
    return Observation(
        observation_id=observation_id,
        person_id=1,
        observation_concept_id=some_concept_id,
        observation_date=date(2020, 6, 1),
        observation_type_concept_id=type_concept_id,
        **value_kwargs,
    )


def _make_measurement(*, measurement_id, type_concept_id, some_concept_id, **value_kwargs):
    return Measurement(
        measurement_id=measurement_id,
        person_id=1,
        measurement_concept_id=some_concept_id,
        measurement_date=date(2020, 6, 1),
        measurement_type_concept_id=type_concept_id,
        **value_kwargs,
    )


class TestObservationNoValueRequirement:
    def test_value_as_string_alone_is_sufficient(self, session, type_concept_id, some_concept_id):
        """OMOP allows Observation's value via value_as_string alone."""
        obs = _make_observation(
            observation_id=9001,
            type_concept_id=type_concept_id,
            some_concept_id=some_concept_id,
            value_as_string="elevated",
        )
        session.add(obs)
        session.commit()
        assert obs.observation_id == 9001

    def test_value_as_number_alone_is_sufficient(self, session, type_concept_id, some_concept_id):
        obs = _make_observation(
            observation_id=9002,
            type_concept_id=type_concept_id,
            some_concept_id=some_concept_id,
            value_as_number=42.0,
        )
        session.add(obs)
        session.commit()
        assert obs.observation_id == 9002

    def test_value_as_concept_id_alone_is_sufficient(
        self, session, type_concept_id, some_concept_id
    ):
        obs = _make_observation(
            observation_id=9003,
            type_concept_id=type_concept_id,
            some_concept_id=some_concept_id,
            value_as_concept_id=some_concept_id,
        )
        session.add(obs)
        session.commit()
        assert obs.observation_id == 9003

    def test_no_value_columns_set_succeeds(self, session, type_concept_id, some_concept_id):
        """Observation has no CDM-mandated value; a bare result row is valid."""
        obs = _make_observation(
            observation_id=9004, type_concept_id=type_concept_id, some_concept_id=some_concept_id
        )
        session.add(obs)
        session.commit()
        assert obs.observation_id == 9004
        assert obs.value_as_number is None
        assert obs.value_as_concept_id is None
        assert obs.value_as_string is None


class TestMeasurementNoValueRequirement:
    def test_no_value_columns_set_succeeds(self, session, type_concept_id, some_concept_id):
        """Measurement has no CDM-mandated value; a bare result row is valid."""
        meas = _make_measurement(
            measurement_id=9001, type_concept_id=type_concept_id, some_concept_id=some_concept_id
        )
        session.add(meas)
        session.commit()
        assert meas.measurement_id == 9001
        assert meas.value_as_number is None
        assert meas.value_as_concept_id is None

    def test_value_as_number_still_settable(self, session, type_concept_id, some_concept_id):
        meas = _make_measurement(
            measurement_id=9002,
            type_concept_id=type_concept_id,
            some_concept_id=some_concept_id,
            value_as_number=98.6,
        )
        session.add(meas)
        session.commit()
        assert meas.value_as_number == 98.6


class TestMetadataNoValueRequirement:
    def test_no_value_columns_set_succeeds(self, session):
        concept_id = _concept_id(session, domain_id="Metadata")
        meta = Metadata(
            metadata_id=9001,
            metadata_concept_id=concept_id,
            metadata_type_concept_id=concept_id,
            name="fixture-metadata",
        )
        session.add(meta)
        session.commit()
        assert meta.metadata_id == 9001
        assert meta.value_as_string is None
