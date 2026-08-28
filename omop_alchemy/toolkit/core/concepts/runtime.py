"""Declarative runtime concept-set inputs for database-side predicates.

``ConceptGroupSpec`` is the right contract for governed omop-semantics units.
``RuntimeConceptSetSpec`` complements it for IDs supplied by configuration at
runtime. It records intent without expanding vocabulary hierarchies or touching
a database; a later query builder renders the corresponding SQL predicate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


def _normalise_concept_ids(values: Iterable[int]) -> tuple[int, ...]:
    """Return stable, duplicate-free inputs without imposing vocabulary policy."""
    return tuple(sorted(set(values)))


@dataclass(frozen=True, slots=True)
class RuntimeConceptSetSpec:
    """Runtime include/exclude inputs for a database-side concept predicate.

    The intended expression is::

        (included ancestor descendants OR included exact IDs)
        AND NOT (excluded ancestor descendants OR excluded exact IDs)

    Exclusion therefore wins if the same concept is reached by both sides.
    Empty inclusions describe an always-false set. Constructing the spec is
    side-effect free and preserves no session-bound vocabulary objects.

    ``require_standard`` and ``include_classification`` deliberately match
    ``ConceptFilter`` and ``ConceptGroupSpec``. A future renderer delegates to
    the existing normalised ``Concept`` flag expressions rather than defining
    another interpretation of OMOP's single-character standardness flags.

    IDs are sorted and deduplicated only. Validity rules for configuration or a
    local vocabulary belong at those boundaries, not in this generic spec.
    """

    include_ancestor_ids: tuple[int, ...] = ()
    include_exact_ids: tuple[int, ...] = ()
    exclude_ancestor_ids: tuple[int, ...] = ()
    exclude_exact_ids: tuple[int, ...] = ()
    require_standard: bool = False
    include_classification: bool = True

    def __post_init__(self) -> None:
        for field_name in (
            "include_ancestor_ids",
            "include_exact_ids",
            "exclude_ancestor_ids",
            "exclude_exact_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _normalise_concept_ids(getattr(self, field_name)),
            )

    @property
    def has_inclusions(self) -> bool:
        """Whether the future predicate can match at least one configured input."""
        return bool(self.include_ancestor_ids or self.include_exact_ids)

    @property
    def requires_concept_join(self) -> bool:
        """Whether standardness filtering requires the Concept table."""
        return self.require_standard
