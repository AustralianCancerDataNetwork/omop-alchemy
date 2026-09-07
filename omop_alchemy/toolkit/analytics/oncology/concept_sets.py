"""Governed oncology concept sets.

Every set here names an omop-semantics semantic unit rather than assembling
concept IDs locally.  That matters beyond tidiness: "what counts as
radiotherapy" is a clinical claim, and it was previously written out by hand
both here and in omop-constructs, governed by neither.  omop-semantics 0.6+
publishes these as governed units, so both consumers name the same definition.

Specs are declarative — importing this module resolves no semantics runtime and
touches no database.  Expansion happens on first use and is cached per
vocabulary by ``toolkit.core.concepts``.

Episode-type concepts are scalars and enums rather than descendant-expanding
groups, so they stay plain accessors below.
"""

from __future__ import annotations

import sqlalchemy.orm as so

from omop_alchemy.toolkit.core._semantics import default_semantics_runtime
from omop_alchemy.toolkit.core.concepts import (
    ConceptGroupSpec,
    ResolvedConceptGroup,
    SemanticUnitRef,
    resolve_concept_group,
)


# Governed concept sets. Names are the governed semantic-unit names, which are
# also the cache keys -- so the cache key derives from the governed identity
# rather than a locally invented label.
RADIOTHERAPY_PROCEDURES = ConceptGroupSpec(
    name="radiotherapy",
    unit=SemanticUnitRef("cancer_procedures", "radiotherapy"),
)

CANCER_INDICATING_SURGERY = ConceptGroupSpec(
    name="cancer_indicating_surgery",
    unit=SemanticUnitRef("cancer_procedures", "cancer_indicating_surgery"),
)

DIAGNOSTIC_STAGING_PROCEDURES = ConceptGroupSpec(
    name="diagnostic_staging_procedure",
    unit=SemanticUnitRef("cancer_procedures", "diagnostic_staging_procedure"),
)

SACT_DRUGS = ConceptGroupSpec(
    name="sact_drug_classification",
    unit=SemanticUnitRef("sact", "sact_drug_classification"),
)

T_STAGE_CONCEPTS = ConceptGroupSpec(
    name="t_stage_concepts",
    unit=SemanticUnitRef("staging", "t_stage_concepts"),
)

N_STAGE_CONCEPTS = ConceptGroupSpec(
    name="n_stage_concepts",
    unit=SemanticUnitRef("staging", "n_stage_concepts"),
)

M_STAGE_CONCEPTS = ConceptGroupSpec(
    name="m_stage_concepts",
    unit=SemanticUnitRef("staging", "m_stage_concepts"),
)

GROUP_STAGE_CONCEPTS = ConceptGroupSpec(
    name="group_stage_concepts",
    unit=SemanticUnitRef("staging", "group_stage_concepts"),
)

TUMOR_GRADE_CONCEPTS = ConceptGroupSpec(
    name="tumor_grade",
    unit=SemanticUnitRef("condition_modifiers", "tumor_grade"),
)

METASTATIC_DISEASE_CONCEPTS = ConceptGroupSpec(
    name="metastatic_disease_concepts",
    unit=SemanticUnitRef("condition_modifiers", "metastatic_disease_concepts"),
)


def resolve_rt_procedure_concept_ids(session: so.Session) -> ResolvedConceptGroup:
    return resolve_concept_group(session, RADIOTHERAPY_PROCEDURES)


def resolve_cancer_indicating_surgery_procedure_concept_ids(
    session: so.Session,
) -> ResolvedConceptGroup:
    return resolve_concept_group(session, CANCER_INDICATING_SURGERY)


def resolve_diagnostic_staging_procedure_concept_ids(
    session: so.Session,
) -> ResolvedConceptGroup:
    return resolve_concept_group(session, DIAGNOSTIC_STAGING_PROCEDURES)


def resolve_sact_drug_concept_ids(session: so.Session) -> ResolvedConceptGroup:
    return resolve_concept_group(session, SACT_DRUGS)


# Episode-type concepts: scalars and enum sets, not descendant-expanding groups.
# ``.ids`` is the correct accessor for an enum-backed unit and is not deprecated.
def disease_episode_type_concept_ids() -> tuple[int, ...]:
    return tuple(default_semantics_runtime().types.disease_episode_types.ids)


def overarching_episode_type_concept_id() -> int:
    return default_semantics_runtime().types.disease_episode_types.episode_of_care


def treatment_episode_type_concept_ids() -> tuple[int, ...]:
    return tuple(default_semantics_runtime().types.treatment_episode_types.ids)


def treatment_regimen_episode_concept_id() -> int:
    return default_semantics_runtime().types.treatment_episode_types.treatment_regimen


def treatment_cycle_episode_concept_id() -> int:
    return default_semantics_runtime().types.treatment_episode_types.treatment_cycle


def laterality_modifier_concept_id() -> int:
    """Governed modifier concept used when a value records laterality."""
    return int(
        default_semantics_runtime().condition_modifiers.condition_modifier_values.laterality
    )


def tumor_size_modifier_concept_id() -> int:
    """Governed numeric modifier concept used for tumour size."""
    return int(
        default_semantics_runtime().condition_modifiers.numeric_condition_modifiers.tumor_size
    )
