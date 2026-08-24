"""Shared CDM concept-table query filtering.

Consolidates filtering logic previously duplicated across downstream packages
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import sqlalchemy as sa

from omop_alchemy.cdm.model.vocabulary import Concept


@dataclass(frozen=True)
class ConceptFilter:
    """Search constraints for plain CDM ``concept``-table queries.

    All fields are optional. Unset fields impose no constraint.

    Attributes
    ----------
    concept_ids : tuple[int, ...], optional
        Restrict results to this set of concept IDs.
    domains : tuple[str, ...], optional
        Restrict results to concepts in these OMOP domains.
    vocabularies : tuple[str, ...], optional
        Restrict results to concepts from these vocabularies.
    require_standard : bool
        When ``True``, only concepts where ``Concept.is_standard`` is
        ``True`` are returned (``standard_concept == 'S'``, tolerating
        surrounding whitespace). Classification concepts (``'C'``) are
        excluded because they are not valid mapping targets. Default ``False``.
    include_classification : bool
        Only meaningful with ``require_standard``. When ``True``, classification
        concepts (``'C'``) are admitted alongside standard ones. Default ``False``.
    require_active : bool
        When ``True``, only concepts where ``Concept.is_valid`` is ``True``
        are returned (``invalid_reason`` is ``NULL``/blank/whitespace, i.e.
        not ``'D'`` or ``'U'``). Default ``False``.
    limit : int, optional
        Maximum number of rows to return. If not set, all matching rows are
        returned.
    """

    concept_ids: Optional[tuple[int, ...]] = None
    domains: Optional[tuple[str, ...]] = None
    vocabularies: Optional[tuple[str, ...]] = None
    require_standard: bool = False
    include_classification: bool = False
    require_active: bool = False
    limit: Optional[int] = None

    def __post_init__(self) -> None:
        if self.limit is not None and self.limit <= 0:
            raise ValueError(
                f"ConceptFilter.limit must be a positive integer, got {self.limit}."
            )

    def apply(self, query: sa.Select) -> sa.Select:
        """Apply filter constraints to a Select already targeting Concept."""
        if self.concept_ids is not None:
            query = query.where(Concept.concept_id.in_(self.concept_ids))

        if self.domains is not None:
            query = query.where(Concept.domain_id.in_(self.domains))

        if self.vocabularies is not None:
            query = query.where(Concept.vocabulary_id.in_(self.vocabularies))

        if self.require_standard:
            query = query.where(
                sa.or_(
                    Concept.is_standard_expr(),
                    Concept.is_classification_expr(),
                )
                if self.include_classification
                else Concept.is_standard_expr()
            )
        if self.require_active:
            query = query.where(Concept.is_valid_expr())

        if self.limit is not None:
            query = query.limit(self.limit)

        return query

    def is_empty(self) -> bool:
        """Return ``True`` if no constraints are set."""
        return (
            self.concept_ids is None
            and self.domains is None
            and self.vocabularies is None
            and not self.require_standard
            and not self.require_active
            and self.limit is None
        )
