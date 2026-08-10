"""Governed oncology concept sets.

Every set here names an omop-semantics semantic unit rather than assembling
concept IDs locally.  That matters beyond tidiness: "what counts as
radiotherapy" is a clinical claim, and it was previously written out by hand
both here and in omop-constructs, governed by neither.  omop-semantics 0.6.0
publishes these as governed units, so both consumers name the same definition.

Specs are declarative — importing this module resolves no semantics runtime and
touches no database.  Expansion happens on first use and is cached per
vocabulary by ``toolkit.core.concepts``.

Episode-type concepts are scalars and enums rather than descendant-expanding
groups, so they stay plain accessors below.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy.orm as so

from omop_alchemy.toolkit.core._semantics import default_semantics_runtime
from omop_alchemy.toolkit.core.concepts import (
    ConceptGroupSpec,
    ResolvedConceptGroup,
    resolve_concept_group,
)


def _unit(value_set_name: str, unit_name: str) -> Any:
    """Resolve a governed semantic unit, lazily.

    Deferred rather than captured at import so that declaring a spec does not
    load the semantics runtime.
    """
    return getattr(getattr(default_semantics_runtime(), value_set_name), unit_name)


class _LazyUnit:
    """Attribute proxy that resolves its semantic unit on first access.

    ``ConceptGroupSpec`` reads ``parent_ids`` / ``excluded_parent_ids`` /
    ``exact_ids`` off its ``unit``.  Holding a proxy rather than the unit itself
    keeps module import free of semantics loading, which is what allows basic
    CDM work to avoid paying for oncology concept sets.
    """

    __slots__ = ("_value_set", "_unit")

    def __init__(self, value_set_name: str, unit_name: str) -> None:
        self._value_set = value_set_name
        self._unit = unit_name

    def __getattr__(self, name: str) -> Any:
        return getattr(_unit(self._value_set, self._unit), name)

    def __repr__(self) -> str:
        return f"<LazyUnit {self._value_set}.{self._unit}>"


# Governed concept sets. Names are the governed semantic-unit names, which are
# also the cache keys -- so the cache key derives from the governed identity
# rather than a locally invented label.
RADIOTHERAPY_PROCEDURES = ConceptGroupSpec(
    name="radiotherapy",
    unit=_LazyUnit("cancer_procedures", "radiotherapy"),
)

CANCER_INDICATING_SURGERY = ConceptGroupSpec(
    name="cancer_indicating_surgery",
    unit=_LazyUnit("cancer_procedures", "cancer_indicating_surgery"),
)

DIAGNOSTIC_STAGING_PROCEDURES = ConceptGroupSpec(
    name="diagnostic_staging_procedure",
    unit=_LazyUnit("cancer_procedures", "diagnostic_staging_procedure"),
)

SACT_DRUGS = ConceptGroupSpec(
    name="sact_drug_classification",
    unit=_LazyUnit("sact", "sact_drug_classification"),
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
