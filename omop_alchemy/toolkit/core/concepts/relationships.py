"""Queries for resolving OMOP concepts to their standard representations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
import sqlalchemy.orm as so

from omop_alchemy.cdm.model.vocabulary import Concept, Concept_Relationship


class StandardConceptMappingColumn(StrEnum):
    """Stable labels emitted by :func:`standard_concept_mapping_select`."""

    source_concept_id = "source_concept_id"
    source_vocabulary_id = "source_vocabulary_id"
    source_concept_code = "source_concept_code"
    source_concept_name = "source_concept_name"
    standard_concept_id = "standard_concept_id"
    standard_vocabulary_id = "standard_vocabulary_id"
    standard_concept_code = "standard_concept_code"
    standard_concept_name = "standard_concept_name"
    relationship_valid_start_date = "relationship_valid_start_date"
    relationship_valid_end_date = "relationship_valid_end_date"


STANDARD_CONCEPT_MAPPING_COLUMNS: tuple[StandardConceptMappingColumn, ...] = tuple(
    StandardConceptMappingColumn
)
"""Columns exposed by a standard concept mapping query."""


STANDARD_CONCEPT_MAPPING_UNIQUENESS: tuple[StandardConceptMappingColumn, ...] = (
    StandardConceptMappingColumn.source_concept_id,
    StandardConceptMappingColumn.standard_concept_id,
)
"""Executable uniqueness key of a standard concept mapping result."""


@dataclass(frozen=True, slots=True)
class StandardConceptMappingSpec:
    """Select valid ``Maps to`` relationships for the requested source concepts.

    An empty ``source_concept_ids`` tuple selects every source. ``valid_on`` can
    be supplied when a reproducible historical vocabulary view is required; it
    applies to both the relationship and its standard target. Invalid
    relationships and targets are always excluded.
    """

    source_concept_ids: tuple[int, ...] = ()
    valid_on: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_concept_ids",
            tuple(sorted(set(self.source_concept_ids))),
        )


def standard_concept_mapping_select(
    spec: StandardConceptMappingSpec,
) -> sa.Select[Any]:
    """Select each valid, single-hop ``Maps to`` standard-concept mapping.

    Results remain relational because OMOP permits one source concept to map to
    more than one standard concept. Standard-concept self-maps are returned as
    ordinary rows; no recursive traversal is required.
    """
    source = so.aliased(Concept, name="mapping_source_concept")
    standard = so.aliased(Concept, name="mapping_standard_concept")
    relationship = so.aliased(
        Concept_Relationship,
        name="standard_mapping_relationship",
    )

    # Separate aliases preserve both sides of the mapping in the result and
    # prevent source predicates from accidentally being applied to the target.
    statement = (
        sa.select(
            source.concept_id.label(
                str(StandardConceptMappingColumn.source_concept_id)
            ),
            source.vocabulary_id.label(
                str(StandardConceptMappingColumn.source_vocabulary_id)
            ),
            source.concept_code.label(
                str(StandardConceptMappingColumn.source_concept_code)
            ),
            source.concept_name.label(
                str(StandardConceptMappingColumn.source_concept_name)
            ),
            standard.concept_id.label(
                str(StandardConceptMappingColumn.standard_concept_id)
            ),
            standard.vocabulary_id.label(
                str(StandardConceptMappingColumn.standard_vocabulary_id)
            ),
            standard.concept_code.label(
                str(StandardConceptMappingColumn.standard_concept_code)
            ),
            standard.concept_name.label(
                str(StandardConceptMappingColumn.standard_concept_name)
            ),
            relationship.valid_start_date.label(
                str(StandardConceptMappingColumn.relationship_valid_start_date)
            ),
            relationship.valid_end_date.label(
                str(StandardConceptMappingColumn.relationship_valid_end_date)
            ),
        )
        .select_from(source)
        .join(relationship, relationship.concept_id_1 == source.concept_id)
        .join(standard, standard.concept_id == relationship.concept_id_2)
        .where(
            relationship.relationship_id == "Maps to",
            relationship.is_valid_expr(),
            standard.is_standard_expr(),
            standard.is_valid_expr(),
        )
    )

    if spec.source_concept_ids:
        statement = statement.where(source.concept_id.in_(spec.source_concept_ids))
    if spec.valid_on is not None:
        # A historical mapping is valid only when both the relationship and
        # the target concept existed at the requested date.
        valid_on = sa.literal(spec.valid_on)
        statement = statement.where(
            relationship.valid_start_date <= valid_on,
            relationship.valid_end_date >= valid_on,
            standard.valid_start_date <= valid_on,
            standard.valid_end_date >= valid_on,
        )
    return statement
