"""Cancer treatment and disease episodes, and the dosing they carry.

Oncology care is structured: a disease episode spans a cancer course, a
treatment regimen sits beneath it, and cycles sit beneath the regimen.
This package provides that structure as navigable objects and answers the
questions that follow from it.

``OncologyEpisode`` is the main entry point.  It classifies itself from
its episode concepts — whether it is a treatment cycle, and which
``OncologyModality`` it represents (systemic therapy, radiotherapy,
surgery, or diagnostic staging) — and exposes the facts it contains::

    from omop_alchemy.toolkit.analytics.oncology import OncologyEpisode

    episode = session.get(OncologyEpisode, episode_id)
    episode.structural_modality
    episode.is_treatment_cycle

Episode traversal resolves to oncology-aware fact classes.
``OncologyDrugExposure`` and ``OncologyProcedure`` extend their CDM
counterparts with domain questions such as whether a row represents
systemic therapy or radiotherapy, so traversal from an episode yields
objects that can answer them.  Non-oncology targets resolve to the base
CDM classes as usual.

**Dosing.**  Radiotherapy and systemic therapy are dosed in
fundamentally different terms and are summarised separately.  RT
summaries group by treatment site; systemic therapy summaries group by
drug or ingredient.  Both report whether the dose is evaluable, because
source units and quantities are frequently not comparable across agents
or regimens — a summary that cannot be interpreted as a dose says so
rather than presenting a total that looks authoritative.

**Concept sets.**  Which concepts constitute systemic therapy,
radiotherapy, cancer-indicating surgery, and diagnostic staging are
governed by ``omop-semantics`` -- this package names those units rather
than assembling concept IDs, so the clinical definitions live in one
reviewable place shared with other consumers.  Expansion against
``concept_ancestor`` happens on first use and is cached per vocabulary, so
recreating an engine does not re-run it.  Each membership check answers both
in Python and in SQL from the same definition.

Governed default concept sets come from ``omop-semantics``, an optional
dependency.  Install ``omop-alchemy[semantics]`` to use them; supply your
own concept sets otherwise.
"""

from .concept_sets import (
    CANCER_INDICATING_SURGERY,
    DIAGNOSTIC_STAGING_PROCEDURES,
    RADIOTHERAPY_PROCEDURES,
    SACT_DRUGS,
    disease_episode_type_concept_ids,
    overarching_episode_type_concept_id,
    resolve_cancer_indicating_surgery_procedure_concept_ids,
    resolve_diagnostic_staging_procedure_concept_ids,
    resolve_rt_procedure_concept_ids,
    resolve_sact_drug_concept_ids,
    treatment_cycle_episode_concept_id,
    treatment_episode_type_concept_ids,
    treatment_regimen_episode_concept_id,
)
from .oncology_critical_weight_loss import OncologyCriticalWeightLossMixin
from .oncology_drug_exposure import OncologyDrugExposure
from .oncology_episodes import OncologyEpisode, OncologyModality
from .oncology_event import OncologyEpisodeEvent, OncologyEpisodeEventMixin
from .oncology_procedure_occurrence import OncologyProcedure
from .oncology_rt_dosing import (
    OncologyRTDosingMixin,
    RTDoseSummary,
    rt_dose_evaluability,
    rt_site_key,
    summarize_rt_procedures,
    summarize_rt_procedures_by,
)
from .oncology_sact_dosing import (
    OncologySACTDosingMixin,
    SACTDoseSummary,
    sact_dose_evaluability,
    summarize_sact_exposures,
    summarize_sact_exposures_by,
)

__all__ = [
    "CANCER_INDICATING_SURGERY",
    "DIAGNOSTIC_STAGING_PROCEDURES",
    "RADIOTHERAPY_PROCEDURES",
    "SACT_DRUGS",
    "OncologyCriticalWeightLossMixin",
    "OncologyDrugExposure",
    "OncologyEpisode",
    "OncologyEpisodeEvent",
    "OncologyEpisodeEventMixin",
    "OncologyModality",
    "OncologyProcedure",
    "OncologyRTDosingMixin",
    "OncologySACTDosingMixin",
    "RTDoseSummary",
    "SACTDoseSummary",
    "disease_episode_type_concept_ids",
    "overarching_episode_type_concept_id",
    "resolve_cancer_indicating_surgery_procedure_concept_ids",
    "resolve_diagnostic_staging_procedure_concept_ids",
    "resolve_rt_procedure_concept_ids",
    "resolve_sact_drug_concept_ids",
    "rt_dose_evaluability",
    "rt_site_key",
    "sact_dose_evaluability",
    "summarize_rt_procedures",
    "summarize_rt_procedures_by",
    "summarize_sact_exposures",
    "summarize_sact_exposures_by",
    "treatment_cycle_episode_concept_id",
    "treatment_episode_type_concept_ids",
    "treatment_regimen_episode_concept_id",
]
