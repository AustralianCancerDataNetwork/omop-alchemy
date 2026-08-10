from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterator

import pytest
import sqlalchemy as sa
import sqlalchemy.orm as so
from orm_loader.helpers import bootstrap

from omop_alchemy.cdm.base import ModifierFieldConcepts
from omop_alchemy.cdm.model import (
    Concept_Ancestor,
    Drug_Exposure,
    Episode,
    Episode_Event,
    Person,
    Procedure_Occurrence,
)
from omop_alchemy.toolkit.analytics.oncology import (
    CANCER_INDICATING_SURGERY,
    RADIOTHERAPY_PROCEDURES,
    SACT_DRUGS,
    OncologyDrugExposure,
    OncologyEpisode,
    OncologyModality,
    OncologyProcedure,
    treatment_cycle_episode_concept_id,
    treatment_regimen_episode_concept_id,
)
from omop_alchemy.toolkit.analytics.oncology import oncology_episodes
from omop_alchemy.toolkit.core.concepts import clear_concept_group_cache
from omop_alchemy.toolkit.episodes.handling import DrugEpisodeMixin


@dataclass
class _ProcedureEvidence:
    is_radiotherapy: bool = False
    is_surgery: bool = False
    is_diagnostic_staging: bool = False


@dataclass
class _DrugEvidence:
    is_sact: bool = False


def _episode_with_events(events: list[object]) -> OncologyEpisode:
    episode = OncologyEpisode()
    episode.__dict__["_linked_oncology_events"] = events
    return episode


@pytest.fixture
def _use_lightweight_event_types(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oncology_episodes, "OncologyProcedure", _ProcedureEvidence)
    monkeypatch.setattr(oncology_episodes, "OncologyDrugExposure", _DrugEvidence)


@pytest.mark.parametrize("reverse", [False, True])
def test_modality_classification_is_independent_of_event_order(
    reverse: bool,
    _use_lightweight_event_types: None,
):
    events: list[object] = [
        _DrugEvidence(is_sact=True),
        _ProcedureEvidence(is_diagnostic_staging=True),
        _ProcedureEvidence(is_surgery=True),
        _ProcedureEvidence(is_radiotherapy=True),
    ]
    if reverse:
        events.reverse()
    episode = _episode_with_events(events)

    expected = frozenset(
        {
            OncologyModality.SACT,
            OncologyModality.DIAGNOSTIC_STAGING,
            OncologyModality.SURGERY,
            OncologyModality.RADIOTHERAPY,
        }
    )
    assert episode.structural_modalities == expected
    assert episode.concept_modalities == expected
    assert episode.structural_modality is OncologyModality.RADIOTHERAPY
    assert episode.concept_modality is OncologyModality.RADIOTHERAPY


def test_structural_and_concept_modalities_preserve_sact_disagreement(
    _use_lightweight_event_types: None,
):
    episode = _episode_with_events([_DrugEvidence(is_sact=False)])

    assert episode.structural_modalities == frozenset({OncologyModality.SACT})
    assert episode.concept_modalities == frozenset()
    assert episode.structural_modality is OncologyModality.SACT
    assert episode.concept_modality is OncologyModality.UNKNOWN


def test_oncology_episode_does_not_expose_generic_drug_episode_interface():
    assert DrugEpisodeMixin not in OncologyEpisode.__mro__
    assert not hasattr(OncologyEpisode, "drug_exposures")


@pytest.fixture
def oncology_session(tmp_path) -> Iterator[so.Session]:
    """Committed oncology graph so vocabulary-cache sessions see its closure."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'oncology.db'}")
    bootstrap(engine, create=True)

    rt_concept_id = 9_100_001
    surgery_concept_id = 9_100_002
    sact_concept_id = 9_100_003
    rt_parent_id = RADIOTHERAPY_PROCEDURES.parent_ids()[0]
    surgery_parent_id = CANCER_INDICATING_SURGERY.parent_ids()[0]
    sact_parent_id = SACT_DRUGS.parent_ids()[0]

    with so.Session(engine) as seed:
        seed.add(
            Person(
                person_id=1,
                year_of_birth=1980,
                gender_concept_id=0,
                race_concept_id=0,
                ethnicity_concept_id=0,
            )
        )
        seed.add_all(
            [
                Episode(
                    episode_id=200,
                    person_id=1,
                    episode_start_date=date(2020, 1, 1),
                    episode_end_date=date(2020, 3, 31),
                    episode_concept_id=treatment_regimen_episode_concept_id(),
                    episode_object_concept_id=0,
                    episode_type_concept_id=0,
                ),
                Episode(
                    episode_id=201,
                    episode_parent_id=200,
                    person_id=1,
                    episode_start_date=date(2020, 1, 2),
                    episode_end_date=date(2020, 1, 31),
                    episode_concept_id=treatment_cycle_episode_concept_id(),
                    episode_object_concept_id=0,
                    episode_type_concept_id=0,
                ),
                Episode(
                    episode_id=202,
                    episode_parent_id=200,
                    person_id=1,
                    episode_start_date=date(2020, 2, 1),
                    episode_end_date=date(2020, 2, 28),
                    episode_concept_id=treatment_cycle_episode_concept_id(),
                    episode_object_concept_id=0,
                    episode_type_concept_id=0,
                ),
            ]
        )
        seed.add_all(
            [
                Procedure_Occurrence(
                    procedure_occurrence_id=11,
                    person_id=1,
                    procedure_concept_id=rt_concept_id,
                    procedure_date=date(2020, 1, 3),
                    procedure_type_concept_id=0,
                ),
                Procedure_Occurrence(
                    procedure_occurrence_id=12,
                    person_id=1,
                    procedure_concept_id=surgery_concept_id,
                    procedure_date=date(2020, 1, 4),
                    procedure_type_concept_id=0,
                ),
                Drug_Exposure(
                    drug_exposure_id=21,
                    person_id=1,
                    drug_concept_id=sact_concept_id,
                    drug_exposure_start_date=date(2020, 2, 2),
                    drug_exposure_end_date=date(2020, 2, 2),
                    drug_type_concept_id=0,
                    quantity=50.0,
                    dose_unit_source_value="mg",
                ),
            ]
        )
        seed.add_all(
            [
                Episode_Event(
                    episode_id=201,
                    event_id=11,
                    episode_event_field_concept_id=(
                        ModifierFieldConcepts.PROCEDURE_OCCURRENCE
                    ),
                ),
                Episode_Event(
                    episode_id=201,
                    event_id=12,
                    episode_event_field_concept_id=(
                        ModifierFieldConcepts.PROCEDURE_OCCURRENCE
                    ),
                ),
                Episode_Event(
                    episode_id=202,
                    event_id=21,
                    episode_event_field_concept_id=(
                        ModifierFieldConcepts.DRUG_EXPOSURE
                    ),
                ),
                Episode_Event(
                    episode_id=200,
                    event_id=999,
                    episode_event_field_concept_id=(
                        ModifierFieldConcepts.PROCEDURE_OCCURRENCE
                    ),
                ),
            ]
        )
        seed.add_all(
            [
                Concept_Ancestor(
                    ancestor_concept_id=rt_parent_id,
                    descendant_concept_id=rt_concept_id,
                    min_levels_of_separation=1,
                    max_levels_of_separation=1,
                ),
                Concept_Ancestor(
                    ancestor_concept_id=surgery_parent_id,
                    descendant_concept_id=surgery_concept_id,
                    min_levels_of_separation=1,
                    max_levels_of_separation=1,
                ),
                Concept_Ancestor(
                    ancestor_concept_id=sact_parent_id,
                    descendant_concept_id=sact_concept_id,
                    min_levels_of_separation=1,
                    max_levels_of_separation=1,
                ),
            ]
        )
        seed.commit()

    clear_concept_group_cache()
    session = so.Session(engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        clear_concept_group_cache()
        engine.dispose()


def test_oncology_episode_traverses_orm_children_and_events(oncology_session: so.Session):
    episode = oncology_session.get(OncologyEpisode, 200)

    assert episode is not None
    assert {child.episode_id for child in episode.children} == {201, 202}
    assert all(isinstance(child, OncologyEpisode) for child in episode.children)
    assert {child.episode_id for child in episode.child_treatment_episodes} == {201, 202}
    assert episode.children[0].primary_episode is episode
    assert not hasattr(episode, "drug_exposures")
    assert episode.structural_modalities == frozenset(
        {
            OncologyModality.RADIOTHERAPY,
            OncologyModality.SURGERY,
            OncologyModality.SACT,
        }
    )
    assert episode.concept_modalities == episode.structural_modalities
    assert episode.structural_modality is OncologyModality.RADIOTHERAPY
    assert episode.concept_modality is OncologyModality.RADIOTHERAPY
    assert [exposure.drug_exposure_id for exposure in episode.sact_exposures] == [21]


def test_oncology_hybrid_membership_agrees_for_instance_and_query(
    oncology_session: so.Session,
):
    procedure = oncology_session.get(OncologyProcedure, 11)
    exposure = oncology_session.get(OncologyDrugExposure, 21)

    assert procedure is not None and procedure.is_radiotherapy
    assert exposure is not None and exposure.is_sact
    assert oncology_session.scalars(
        sa.select(OncologyProcedure).where(OncologyProcedure.is_radiotherapy)
    ).all() == [procedure]
    assert oncology_session.scalars(
        sa.select(OncologyDrugExposure).where(OncologyDrugExposure.is_sact)
    ).all() == [exposure]


def test_detached_oncology_membership_and_episode_classification_fail_loudly(
    oncology_session: so.Session,
):
    procedure = oncology_session.get(OncologyProcedure, 11)
    episode = oncology_session.get(OncologyEpisode, 202)
    assert procedure is not None and episode is not None

    oncology_session.expunge(procedure)
    oncology_session.expunge(episode)

    with pytest.raises(RuntimeError, match="not attached to a Session"):
        _ = procedure.is_radiotherapy
    with pytest.raises(RuntimeError, match="not attached to a Session"):
        _ = episode.structural_modalities


def test_oncology_episode_event_reports_dangling_target(oncology_session: so.Session):
    episode = oncology_session.get(OncologyEpisode, 200)

    assert episode is not None
    assert len(episode.episode_events) == 1
    assert [
        diagnostic.kind
        for diagnostic in episode.episode_events[0].event_resolution_diagnostics
    ] == ["dangling_event"]
