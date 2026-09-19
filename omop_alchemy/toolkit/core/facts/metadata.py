"""Immutable metadata connecting OMOP Domains to existing event models."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from sqlalchemy.sql.selectable import FromClause, SelectBase

from omop_alchemy.cdm.base.errors import UnsupportedModelError
from omop_alchemy.cdm.model import Condition_Occurrence
from omop_alchemy.cdm.model.clinical.event_metadata import clinical_event_model_spec

from omop_alchemy.toolkit.core.events import ClinicalEventIdentity

from .contracts import FactIdentity

CONDITION_DOMAIN_CONCEPT_ID = 19


class UnsupportedFactDomainError(UnsupportedModelError):
    """Raised when a model or Domain is outside the fact-query pilot."""

    model_kind = "fact domain"


@dataclass(frozen=True, slots=True)
class FactDomainSpec:
    """The governed Domain identity and existing model used for a fact kind."""

    domain_concept_id: int
    domain_name: str
    model: type[Any]

    def __post_init__(self) -> None:
        if self.domain_concept_id <= 0:
            raise ValueError("domain_concept_id must be positive")
        if not self.domain_name.strip():
            raise ValueError("domain_name must not be empty")
        clinical_event_model_spec(self.model)


CONDITION_FACT_DOMAIN = FactDomainSpec(
    domain_concept_id=CONDITION_DOMAIN_CONCEPT_ID,
    domain_name="Condition",
    model=Condition_Occurrence,
)

FACT_DOMAINS_BY_CONCEPT_ID: Mapping[int, FactDomainSpec] = MappingProxyType(
    {CONDITION_FACT_DOMAIN.domain_concept_id: CONDITION_FACT_DOMAIN}
)
FACT_DOMAINS_BY_NAME: Mapping[str, FactDomainSpec] = MappingProxyType(
    {
        CONDITION_FACT_DOMAIN.domain_name.casefold(): CONDITION_FACT_DOMAIN,
        Condition_Occurrence.__tablename__.casefold(): CONDITION_FACT_DOMAIN,
    }
)


def fact_domain_spec(
    domain: FactDomainSpec | type[Any] | int | str,
) -> FactDomainSpec:
    """Resolve a supported fact Domain without opening a database connection."""
    if isinstance(domain, FactDomainSpec):
        return domain
    if isinstance(domain, int):
        spec = FACT_DOMAINS_BY_CONCEPT_ID.get(domain)
    elif isinstance(domain, str):
        spec = FACT_DOMAINS_BY_NAME.get(domain.casefold())
    elif isinstance(domain, type):
        clinical_event_model_spec(domain)
        spec = FACT_DOMAINS_BY_NAME.get(str(domain.__tablename__).casefold())
    else:
        spec = None
    if spec is None:
        raise UnsupportedFactDomainError(
            domain,
            "only the Condition Domain is registered in the initial pilot",
        )
    return spec


@dataclass(frozen=True, slots=True)
class FactSource:
    """A fact projection paired with its Domain and completeness guarantee."""

    source: type[Any] | FromClause | SelectBase
    domain: FactDomainSpec
    complete: bool


def fact_source(
    source: type[Any] | FromClause | SelectBase,
    *,
    domain: FactDomainSpec | type[Any] | int | str | None = None,
    complete: bool | None = None,
) -> FactSource:
    """Describe an ORM fact table or a caller-scoped fact projection.

    ORM models are complete by default. Selectables are incomplete by default
    because filtering may explain an absent endpoint; callers should assert
    ``complete=True`` only when the selectable represents the whole Domain.
    """
    if isinstance(source, type):
        inferred = fact_domain_spec(source)
        resolved = inferred if domain is None else fact_domain_spec(domain)
        if resolved != inferred:
            raise ValueError("fact source model does not belong to the supplied Domain")
        return FactSource(
            source=source,
            domain=resolved,
            complete=True if complete is None else complete,
        )
    if not isinstance(source, (FromClause, SelectBase)):
        raise TypeError("fact source must be a mapped model, Select, or FromClause")
    if domain is None:
        raise ValueError("selectable fact sources require an explicit Domain")
    return FactSource(
        source=source,
        domain=fact_domain_spec(domain),
        complete=False if complete is None else complete,
    )


def fact_identity_from_event(
    identity: ClinicalEventIdentity,
    domain: FactDomainSpec | type[Any] | int | str,
) -> FactIdentity:
    """Convert a table-scoped event identity to its Domain-scoped identity."""
    spec = fact_domain_spec(domain)
    table = clinical_event_model_spec(spec.model).event_source_table
    if identity.event_source_table != table:
        raise ValueError(
            f"{identity.event_source_table!r} does not belong to Domain {spec.domain_name}"
        )
    return FactIdentity(spec.domain_concept_id, identity.event_id)


def clinical_event_identity(identity: FactIdentity) -> ClinicalEventIdentity:
    """Convert a registered fact identity back to a clinical-event identity."""
    spec = fact_domain_spec(identity.domain_concept_id)
    table = clinical_event_model_spec(spec.model).event_source_table
    return ClinicalEventIdentity(table, identity.fact_id)
