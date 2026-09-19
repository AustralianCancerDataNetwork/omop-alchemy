"""Canonical contracts and query builders for OMOP Fact Relationships."""

from .contracts import (
    FactColumn,
    FactIdentity,
    FactRelationshipColumn,
    FactRelationshipDiagnostic,
    FactRelationshipDiagnosticCode,
    FactRelationshipDiagnosticColumn,
    FactRelationshipQueries,
)
from .metadata import (
    CONDITION_DOMAIN_CONCEPT_ID,
    CONDITION_FACT_DOMAIN,
    FactDomainSpec,
    FactSource,
    UnsupportedFactDomainError,
    clinical_event_identity,
    fact_domain_spec,
    fact_identity_from_event,
    fact_source,
)
from .projections import InvalidFactSourceError, canonical_fact_projection
from .queries import fact_relationship_queries
from .registry import (
    CanonicalFactRelationshipSpec,
    FactRelationshipKind,
    FactRelationshipRegistry,
    MissingGovernedFactRelationshipConceptsError,
    RelationshipConceptDefinition,
    canonical_fact_relationship_values,
    condition_etiology_registry,
    default_fact_relationship_registry,
)

__all__ = [
    "CONDITION_DOMAIN_CONCEPT_ID",
    "CONDITION_FACT_DOMAIN",
    "CanonicalFactRelationshipSpec",
    "FactColumn",
    "FactDomainSpec",
    "FactIdentity",
    "FactRelationshipColumn",
    "FactRelationshipDiagnostic",
    "FactRelationshipDiagnosticCode",
    "FactRelationshipDiagnosticColumn",
    "FactRelationshipKind",
    "FactRelationshipQueries",
    "FactRelationshipRegistry",
    "FactSource",
    "InvalidFactSourceError",
    "MissingGovernedFactRelationshipConceptsError",
    "RelationshipConceptDefinition",
    "UnsupportedFactDomainError",
    "canonical_fact_projection",
    "canonical_fact_relationship_values",
    "clinical_event_identity",
    "condition_etiology_registry",
    "default_fact_relationship_registry",
    "fact_domain_spec",
    "fact_identity_from_event",
    "fact_relationship_queries",
    "fact_source",
]
