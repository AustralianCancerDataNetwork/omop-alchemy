"""Side-effect-free contracts for cross-domain OMOP fact relationships."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

import sqlalchemy as sa


class FactColumn(StrEnum):
    """Labels added to a canonical clinical-event projection."""

    domain_concept_id = "domain_concept_id"


@dataclass(frozen=True, order=True, slots=True)
class FactIdentity:
    """OMOP fact identity, scoped by Domain concept rather than table alone."""

    domain_concept_id: int
    fact_id: int

    def __post_init__(self) -> None:
        invalid = [
            name
            for name, value in (
                ("domain_concept_id", self.domain_concept_id),
                ("fact_id", self.fact_id),
            )
            if value <= 0
        ]
        if invalid:
            raise ValueError(f"{', '.join(invalid)} must be positive")


class FactRelationshipColumn(StrEnum):
    """Stable labels emitted by a resolved fact-relationship query."""

    relationship_concept_id = "relationship_concept_id"
    from_domain_concept_id = "from_domain_concept_id"
    from_fact_id = "from_fact_id"
    from_person_id = "from_person_id"
    from_fact_date = "from_fact_date"
    from_fact_datetime = "from_fact_datetime"
    from_fact_concept_id = "from_fact_concept_id"
    from_fact_field_concept_id = "from_fact_field_concept_id"
    from_fact_source_table = "from_fact_source_table"
    to_domain_concept_id = "to_domain_concept_id"
    to_fact_id = "to_fact_id"
    to_person_id = "to_person_id"
    to_fact_date = "to_fact_date"
    to_fact_datetime = "to_fact_datetime"
    to_fact_concept_id = "to_fact_concept_id"
    to_fact_field_concept_id = "to_fact_field_concept_id"
    to_fact_source_table = "to_fact_source_table"


class FactRelationshipDiagnosticCode(StrEnum):
    """Reasons a stored relationship was excluded from resolved matches."""

    missing_from_fact = "missing_from_fact"
    missing_to_fact = "missing_to_fact"
    person_mismatch = "person_mismatch"


class FactRelationshipDiagnosticColumn(StrEnum):
    """Stable labels emitted by the advisory diagnostic query."""

    diagnostic_code = "diagnostic_code"
    relationship_concept_id = "relationship_concept_id"
    from_domain_concept_id = "from_domain_concept_id"
    from_fact_id = "from_fact_id"
    to_domain_concept_id = "to_domain_concept_id"
    to_fact_id = "to_fact_id"
    message = "message"


@dataclass(frozen=True, slots=True)
class FactRelationshipDiagnostic:
    """Typed value adapter for one diagnostic result mapping."""

    diagnostic_code: FactRelationshipDiagnosticCode
    relationship_concept_id: int
    from_fact: FactIdentity
    to_fact: FactIdentity
    message: str

    @classmethod
    def from_mapping(cls, row: Mapping[Any, Any]) -> FactRelationshipDiagnostic:
        """Build a typed diagnostic from a SQLAlchemy result mapping."""
        return cls(
            diagnostic_code=FactRelationshipDiagnosticCode(
                row[str(FactRelationshipDiagnosticColumn.diagnostic_code)]
            ),
            relationship_concept_id=row[
                str(FactRelationshipDiagnosticColumn.relationship_concept_id)
            ],
            from_fact=FactIdentity(
                row[str(FactRelationshipDiagnosticColumn.from_domain_concept_id)],
                row[str(FactRelationshipDiagnosticColumn.from_fact_id)],
            ),
            to_fact=FactIdentity(
                row[str(FactRelationshipDiagnosticColumn.to_domain_concept_id)],
                row[str(FactRelationshipDiagnosticColumn.to_fact_id)],
            ),
            message=row[str(FactRelationshipDiagnosticColumn.message)],
        )


@dataclass(frozen=True, slots=True)
class FactRelationshipQueries:
    """Resolved matches plus an optional advisory diagnostic query."""

    matches: sa.Select[Any]
    diagnostics: sa.Select[Any] | sa.CompoundSelect[Any] | None = None
