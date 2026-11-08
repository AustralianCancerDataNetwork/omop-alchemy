"""Side-effect-free contracts for canonical cross-table clinical events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable


class ClinicalEventColumn(StrEnum):
    """Canonical labels emitted by a cross-table clinical-event projection."""

    person_id = "person_id"
    event_id = "event_id"
    event_date = "event_date"
    event_datetime = "event_datetime"
    event_concept_id = "event_concept_id"
    event_field_concept_id = "event_field_concept_id"
    event_source_table = "event_source_table"
    value_as_number = "value_as_number"
    value_as_concept_id = "value_as_concept_id"
    unit_concept_id = "unit_concept_id"

    @classmethod
    def required_columns(cls) -> tuple[ClinicalEventColumn, ...]:
        """Required projection labels in row-contract order."""
        return tuple(cls[name] for name in ClinicalEventRow.__annotations__)

    @classmethod
    def optional_columns(cls) -> tuple[ClinicalEventColumn, ...]:
        """Nullable value labels, excluding inherited required fields."""
        return tuple(cls[name] for name in ValuedClinicalEventRow.__annotations__)


@runtime_checkable
class ClinicalEventRow(Protocol):
    """Value-level view of the required canonical event projection.

    SQLAlchemy ``Row`` objects and small dataclasses can both satisfy this
    protocol. It describes the output consumed by downstream tools; it does not
    require a session-bound ORM entity.
    """

    person_id: int
    event_id: int
    event_date: date
    event_datetime: datetime | None
    event_concept_id: int
    event_field_concept_id: int
    event_source_table: str


@runtime_checkable
class ValuedClinicalEventRow(ClinicalEventRow, Protocol):
    """Canonical event row extended with nullable value and unit fields."""

    value_as_number: float | None
    value_as_concept_id: int | None
    unit_concept_id: int | None


@dataclass(frozen=True, order=True, slots=True)
class ClinicalEventIdentity:
    """Cross-table event identity.

    OMOP event IDs are unique only within their source table. A Measurement and
    a Procedure Occurrence may legitimately have the same numeric ID, so the
    table is a mandatory part of identity.
    """

    event_source_table: str
    event_id: int

    def __post_init__(self) -> None:
        if not self.event_source_table.strip():
            raise ValueError("event_source_table must not be empty")
