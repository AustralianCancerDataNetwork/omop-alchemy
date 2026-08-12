"""Governed concept groups and their database-backed expansion.

omop-semantics publishes *anchors*: governed concept IDs for named clinical
ideas, with no database access.  Expanding an anchor set into the concepts that
actually appear in data is vocabulary-release-specific, so it belongs here
rather than in the portable package.

``ConceptGroupSpec`` names a governed semantic unit and how to expand it.
``ResolvedConceptGroup`` is the expansion, cached per vocabulary, and answers
membership two ways from one definition::

    group = resolve_concept_group(session, SACT_DRUGS)

    drug.drug_concept_id in group            # Python, O(1)
    group.expression(Drug_Exposure.drug_concept_id)   # SQL, evaluated per query

Both renderings come from the same spec, so they cannot disagree.  The SQL form
deliberately re-derives a subquery rather than embedding the resolved IDs as a
literal ``IN`` list: a closure of tens of thousands of IDs makes for poor query
plans and can exceed driver parameter limits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import sqlalchemy as sa
import sqlalchemy.orm as so

from omop_alchemy.cdm.model import Concept_Ancestor


@dataclass(frozen=True)
class ConceptGroupSpec:
    """A governed omop-semantics semantic unit plus how to expand it.

    Declarative and side-effect free — constructing one performs no I/O and
    touches no database.

    Parameters
    ----------
    name
        Stable identifier for this group, used as the cache key.  Use the
        governed semantic-unit name so the key derives from the governed
        identity rather than a locally invented label.
    unit
        The omop-semantics ``RuntimeSemanticUnit`` supplying anchors.  Read
        lazily, so a spec can be declared at module scope without loading the
        semantics runtime.  omop-semantics 0.6.0 put mixed-role composition on
        the semantic unit rather than on ``RuntimeGroup``, which is why this
        takes a unit: ``parent_ids`` expand through descendants while
        ``exact_ids`` are matched directly.
    include_descendants
        Expand ``parent_ids`` through ``concept_ancestor``.  When False only
        the anchors themselves are members.
    standard_only
        Restrict expansion to standard concepts.  Defaults to False, matching
        the historical oncology behaviour of reading ``concept_ancestor``
        without a standard filter.  Note this is the *opposite* default to
        ``OMOPConceptSource.descendants``; the difference is deliberate and
        declared here rather than left implicit.
    """

    name: str
    unit: Any
    include_descendants: bool = True
    standard_only: bool = False

    def parent_ids(self) -> tuple[int, ...]:
        """Governed descendant-expanding anchors."""
        return tuple(sorted(self.unit.parent_ids))

    def excluded_parent_ids(self) -> tuple[int, ...]:
        """Governed anchors whose descendants are subtracted."""
        return tuple(sorted(self.unit.excluded_parent_ids))

    def exact_ids(self) -> tuple[int, ...]:
        """Governed members matched directly, without descendant expansion."""
        return tuple(sorted(self.unit.exact_ids))

    def expression_for(
        self,
        column: sa.SQLColumnExpression[Any],
    ) -> sa.ColumnElement[bool]:
        """SQL membership for this group, as a subquery over ``concept_ancestor``.

        Available on the spec because the SQL form needs no session: the
        traversal is performed by the database when the query runs.  That is
        what lets a ``hybrid_property`` expose the same governed set at class
        level, where no session exists.

        Deliberately a subquery rather than a literal ``IN`` list built from a
        resolved group: a closure of tens of thousands of IDs degrades query
        plans and can exceed driver parameter limits.
        """
        parents = self.parent_ids()
        exact = self.exact_ids()

        clauses: list[sa.ColumnElement[bool]] = []

        if parents and self.include_descendants:
            expr = column.in_(
                _descendant_select(parents, standard_only=self.standard_only)
            )
            excluded = self.excluded_parent_ids()
            if excluded:
                expr = expr & column.not_in(
                    _descendant_select(excluded, standard_only=self.standard_only)
                )
            clauses.append(expr)
        elif parents:
            clauses.append(column.in_(parents))

        if exact:
            clauses.append(column.in_(exact))

        if not clauses:
            return sa.false()
        return sa.or_(*clauses)


def _descendant_select(
    parents: Iterable[int],
    *,
    standard_only: bool,
) -> sa.Select:
    stmt = sa.select(Concept_Ancestor.descendant_concept_id).where(
        Concept_Ancestor.ancestor_concept_id.in_(tuple(parents))
    )
    if standard_only:
        from omop_alchemy.cdm.model.vocabulary import Concept

        stmt = stmt.join(
            Concept,
            Concept.concept_id == Concept_Ancestor.descendant_concept_id,
        ).where(Concept.is_standard_expr())
    return stmt


@dataclass(frozen=True)
class ResolvedConceptGroup:
    """A governed group expanded against one vocabulary.

    Membership is
    ``(descendants(parents) - descendants(excluded parents)) | exact_ids``.
    Exact members are explicit inclusions and are never subtracted by the
    exclusion step, matching omop-semantics' composition contract.
    """

    spec: ConceptGroupSpec
    ids: frozenset[int] = field(repr=False)

    def __contains__(self, concept_id: int | None) -> bool:
        return concept_id is not None and concept_id in self.ids

    def __len__(self) -> int:
        return len(self.ids)

    def __repr__(self) -> str:
        return f"<ResolvedConceptGroup {self.spec.name!r} ids={len(self.ids)}>"

    def estimated_bytes(self) -> int:
        """Approximate retained size, for cache accounting.

        Measured at ~80 bytes per ID for a ``frozenset`` of OMOP-magnitude
        concept IDs: a distinct ``PyLong`` each, plus hash-table overhead at a
        0.6 load factor.
        """
        return 80 * len(self.ids)

    def expression(
        self,
        column: sa.SQLColumnExpression[Any],
    ) -> sa.ColumnElement[bool]:
        """SQL membership for this group — delegates to the spec.

        Kept here so a caller holding a resolved group can reach either
        rendering without going back to the spec, but the definition lives in
        one place.
        """
        return self.spec.expression_for(column)


def build_concept_group(
    session: so.Session,
    spec: ConceptGroupSpec,
) -> ResolvedConceptGroup:
    """Expand a governed group against the vocabulary behind ``session``.

    Performs up to two queries — the include closure and, when the group
    declares exclusions, the exclude closure.  Callers should normally go
    through the cache in ``registry`` rather than calling this directly.
    """
    parents = spec.parent_ids()
    ids: set[int] = set()

    if parents:
        if spec.include_descendants:
            stmt = _descendant_select(parents, standard_only=spec.standard_only)
            ids |= set(session.execute(stmt).scalars().all())
            excluded = spec.excluded_parent_ids()
            if excluded:
                stmt = _descendant_select(excluded, standard_only=spec.standard_only)
                ids -= set(session.execute(stmt).scalars().all())
        else:
            ids |= set(parents)

    # Exact members are explicit inclusions, applied after any subtraction.
    ids |= set(spec.exact_ids())

    return ResolvedConceptGroup(spec=spec, ids=frozenset(ids))
