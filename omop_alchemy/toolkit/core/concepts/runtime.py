"""Declarative runtime concept-set inputs for database-side predicates.

``ConceptGroupSpec`` is the right contract for governed omop-semantics units.
``RuntimeConceptSetSpec`` complements it for IDs supplied by configuration at
runtime. It records intent without expanding vocabulary hierarchies or touching
a database; ``runtime_concept_predicate`` renders the corresponding SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import sqlalchemy as sa

from omop_alchemy.cdm.model.vocabulary import Concept, Concept_Ancestor
from omop_alchemy.cdm.query import ConceptFilter


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
    ``ConceptFilter`` and ``ConceptGroupSpec``. Predicate rendering delegates to
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
        """Whether the predicate can match at least one configured input."""
        return bool(self.include_ancestor_ids or self.include_exact_ids)


def descendant_concept_select(
    ancestor_ids: Iterable[int],
    *,
    require_standard: bool = False,
    include_classification: bool = True,
) -> sa.Select[Any]:
    """Select each descendant ID once for the supplied ancestors."""
    statement = (
        sa.select(Concept_Ancestor.descendant_concept_id)
        .where(Concept_Ancestor.ancestor_concept_id.in_(tuple(ancestor_ids)))
        .distinct()
    )
    if not require_standard:
        return statement

    statement = statement.join(
        Concept,
        Concept.concept_id == Concept_Ancestor.descendant_concept_id,
    )
    return ConceptFilter(
        require_standard=True,
        include_classification=include_classification,
    ).apply(statement)


def _concept_set_side(
    column: sa.SQLColumnExpression[Any],
    *,
    ancestor_ids: tuple[int, ...],
    exact_ids: tuple[int, ...],
) -> sa.ColumnElement[bool]:
    clauses: list[sa.ColumnElement[bool]] = []
    if ancestor_ids:
        clauses.append(column.in_(descendant_concept_select(ancestor_ids)))
    if exact_ids:
        clauses.append(column.in_(exact_ids))
    return sa.or_(*clauses) if clauses else sa.false()


def runtime_concept_predicate(
    column: sa.SQLColumnExpression[Any],
    spec: RuntimeConceptSetSpec,
) -> sa.ColumnElement[bool]:
    """Render runtime concept membership entirely as database predicates."""
    included = _concept_set_side(
        column,
        ancestor_ids=spec.include_ancestor_ids,
        exact_ids=spec.include_exact_ids,
    )
    if not spec.has_inclusions:
        return sa.false()

    excluded = _concept_set_side(
        column,
        ancestor_ids=spec.exclude_ancestor_ids,
        exact_ids=spec.exclude_exact_ids,
    )
    predicate = sa.and_(included, sa.not_(excluded))

    if spec.require_standard:
        standard_concept_ids = ConceptFilter(
            require_standard=True,
            include_classification=spec.include_classification,
        ).apply(sa.select(Concept.concept_id))
        predicate = sa.and_(
            predicate,
            column.in_(standard_concept_ids),
        )
    return predicate
