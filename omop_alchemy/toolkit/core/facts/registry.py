"""Governed, immutable relationship metadata for the Condition pilot."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from types import MappingProxyType
from typing import Mapping

from omop_alchemy.toolkit.core._semantics import default_semantics_runtime

from .contracts import FactIdentity
from .metadata import CONDITION_DOMAIN_CONCEPT_ID


class FactRelationshipKind(StrEnum):
    """Supported canonical relationship meanings."""

    primary_etiology = "primary_etiology"
    contributing_etiology = "contributing_etiology"
    non_contributing = "non_contributing"


class MissingGovernedFactRelationshipConceptsError(LookupError):
    """Raised when the optional semantics package has no governed IDs yet."""


@dataclass(frozen=True, slots=True)
class RelationshipConceptDefinition:
    """One relationship Concept and its required inverse Concept."""

    concept_id: int
    inverse_concept_id: int
    label: str

    def __post_init__(self) -> None:
        if self.concept_id <= 0 or self.inverse_concept_id <= 0:
            raise ValueError("relationship Concept IDs must be positive")
        if self.concept_id == self.inverse_concept_id:
            raise ValueError("a relationship Concept cannot be its own inverse")
        if not self.label.strip():
            raise ValueError("relationship label must not be empty")


@dataclass(frozen=True, slots=True)
class CanonicalFactRelationshipSpec:
    """The one writable direction for a supported relationship meaning."""

    kind: FactRelationshipKind
    relationship_concept_id: int
    from_domain_concept_id: int
    to_domain_concept_id: int


def _validate_concept_definitions(
    concepts: Mapping[int, RelationshipConceptDefinition],
) -> None:
    for concept_id, definition in concepts.items():
        if concept_id != definition.concept_id:
            raise ValueError("relationship Concept key does not match its definition")
        inverse = concepts.get(definition.inverse_concept_id)
        if inverse is None or inverse.inverse_concept_id != concept_id:
            raise ValueError(
                f"relationship Concept {concept_id} has no symmetric inverse"
            )


def _validate_canonical_specs(
    canonical: Mapping[FactRelationshipKind, CanonicalFactRelationshipSpec],
    concepts: Mapping[int, RelationshipConceptDefinition],
) -> None:
    if set(canonical) != set(FactRelationshipKind):
        raise ValueError("canonical registry must define every relationship kind")
    forward_ids: set[int] = set()
    for kind, spec in canonical.items():
        if spec.kind != kind:
            raise ValueError("canonical relationship key does not match its spec")
        if spec.relationship_concept_id not in concepts:
            raise ValueError("canonical relationship Concept is not registered")
        if spec.relationship_concept_id in forward_ids:
            raise ValueError("canonical relationship Concept IDs must be unique")
        if min(spec.from_domain_concept_id, spec.to_domain_concept_id) <= 0:
            raise ValueError("relationship Domain concept IDs must be positive")
        forward_ids.add(spec.relationship_concept_id)


@dataclass(frozen=True, slots=True)
class FactRelationshipRegistry:
    """Immutable six-Concept vocabulary plus three canonical write specs."""

    concepts_by_id: Mapping[int, RelationshipConceptDefinition]
    canonical_by_kind: Mapping[FactRelationshipKind, CanonicalFactRelationshipSpec]

    def __post_init__(self) -> None:
        concepts = dict(self.concepts_by_id)
        canonical = dict(self.canonical_by_kind)
        _validate_concept_definitions(concepts)
        _validate_canonical_specs(canonical, concepts)
        object.__setattr__(self, "concepts_by_id", MappingProxyType(concepts))
        object.__setattr__(self, "canonical_by_kind", MappingProxyType(canonical))

    def resolve(
        self, kind: FactRelationshipKind | str
    ) -> CanonicalFactRelationshipSpec:
        """Resolve an enum or its stable string value to a canonical spec."""
        return self.canonical_by_kind[FactRelationshipKind(kind)]


def condition_etiology_registry(
    *,
    primary_etiology_of: int,
    has_primary_etiology: int,
    contributing_etiology_of: int,
    has_contributing_etiology: int,
    non_contributing_to: int,
    has_non_contributing_condition: int,
) -> FactRelationshipRegistry:
    """Build the Condition pilot registry from six governed Concept IDs."""
    pairs = (
        (
            primary_etiology_of,
            has_primary_etiology,
            "Primary etiology of",
            "Has primary etiology",
        ),
        (
            contributing_etiology_of,
            has_contributing_etiology,
            "Contributing etiology of",
            "Has contributing etiology",
        ),
        (
            non_contributing_to,
            has_non_contributing_condition,
            "Non-contributing to",
            "Has non-contributing condition",
        ),
    )
    concepts: dict[int, RelationshipConceptDefinition] = {}
    for forward_id, inverse_id, forward_label, inverse_label in pairs:
        concepts[forward_id] = RelationshipConceptDefinition(
            forward_id, inverse_id, forward_label
        )
        concepts[inverse_id] = RelationshipConceptDefinition(
            inverse_id, forward_id, inverse_label
        )
    domain_id = CONDITION_DOMAIN_CONCEPT_ID
    canonical = {
        FactRelationshipKind.primary_etiology: CanonicalFactRelationshipSpec(
            FactRelationshipKind.primary_etiology,
            primary_etiology_of,
            domain_id,
            domain_id,
        ),
        FactRelationshipKind.contributing_etiology: CanonicalFactRelationshipSpec(
            FactRelationshipKind.contributing_etiology,
            contributing_etiology_of,
            domain_id,
            domain_id,
        ),
        FactRelationshipKind.non_contributing: CanonicalFactRelationshipSpec(
            FactRelationshipKind.non_contributing,
            non_contributing_to,
            domain_id,
            domain_id,
        ),
    }
    return FactRelationshipRegistry(concepts, canonical)


@cache
def default_fact_relationship_registry() -> FactRelationshipRegistry:
    """Load governed IDs lazily when omop-semantics publishes this value set."""
    runtime = default_semantics_runtime()
    value_set = getattr(runtime, "fact_relationships", None)
    values = getattr(value_set, "relationship_types", None)
    names = (
        "primary_etiology_of",
        "has_primary_etiology",
        "contributing_etiology_of",
        "has_contributing_etiology",
        "non_contributing_to",
        "has_non_contributing_condition",
    )
    if values is None or any(not hasattr(values, name) for name in names):
        raise MissingGovernedFactRelationshipConceptsError(
            "omop-semantics does not yet publish the six governed fact "
            "relationship Concept IDs; inject condition_etiology_registry(...) "
            "until governance is complete"
        )

    def concept_id(name: str) -> int:
        value = getattr(values, name)
        return int(getattr(value, "concept_id", value))

    return condition_etiology_registry(
        primary_etiology_of=concept_id("primary_etiology_of"),
        has_primary_etiology=concept_id("has_primary_etiology"),
        contributing_etiology_of=concept_id("contributing_etiology_of"),
        has_contributing_etiology=concept_id("has_contributing_etiology"),
        non_contributing_to=concept_id("non_contributing_to"),
        has_non_contributing_condition=concept_id("has_non_contributing_condition"),
    )


def canonical_fact_relationship_values(
    from_fact: FactIdentity,
    to_fact: FactIdentity,
    *,
    relationship: FactRelationshipKind | str,
    registry: FactRelationshipRegistry | None = None,
) -> dict[str, int]:
    """Return one canonical ``Fact_Relationship`` row mapping."""
    active = registry or default_fact_relationship_registry()
    spec = active.resolve(relationship)
    if from_fact.domain_concept_id != spec.from_domain_concept_id:
        raise ValueError("from_fact belongs to the wrong Domain")
    if to_fact.domain_concept_id != spec.to_domain_concept_id:
        raise ValueError("to_fact belongs to the wrong Domain")
    return {
        "domain_concept_id_1": from_fact.domain_concept_id,
        "fact_id_1": from_fact.fact_id,
        "domain_concept_id_2": to_fact.domain_concept_id,
        "fact_id_2": to_fact.fact_id,
        "relationship_concept_id": spec.relationship_concept_id,
    }
