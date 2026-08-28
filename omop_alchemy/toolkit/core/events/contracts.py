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


CANONICAL_EVENT_REQUIRED_COLUMNS: tuple[ClinicalEventColumn, ...] = (
    ClinicalEventColumn.person_id,
    ClinicalEventColumn.event_id,
    ClinicalEventColumn.event_date,
    ClinicalEventColumn.event_datetime,
    ClinicalEventColumn.event_concept_id,
    ClinicalEventColumn.event_field_concept_id,
    ClinicalEventColumn.event_source_table,
)
"""Columns every canonical clinical-event projection must expose."""


CANONICAL_EVENT_OPTIONAL_COLUMNS: tuple[ClinicalEventColumn, ...] = (
    ClinicalEventColumn.value_as_number,
    ClinicalEventColumn.value_as_concept_id,
    ClinicalEventColumn.unit_concept_id,
)
"""Nullable value columns a projection may add when its source supports them."""


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
