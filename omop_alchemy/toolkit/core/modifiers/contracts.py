"""Side-effect-free contracts for canonical OMOP modifier rows.

OMOP represents a modifier as an ordinary Measurement or Observation carrying
a polymorphic link to another row. Both ends of that relationship have scoped
identities:

* a modifier ID is unique only within its source table; and
* a target event ID is meaningful only alongside the OMOP Field concept naming
  the target table's primary-key field.

The contracts below preserve those scopes after heterogeneous source tables are
projected into one query. They intentionally contain no ORM model registry;
physical model metadata lives in :mod:`.metadata`, while this module remains a
small vocabulary for rows, identities, selection, and diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol, runtime_checkable


class ModifierColumn(StrEnum):
    """Stable labels emitted by Measurement/Observation modifier projections.

    Native columns such as ``measurement_event_id`` and
    ``observation_event_id`` converge on these names so downstream query code
    never needs to branch on the physical modifier source.
    """

    person_id = "person_id"
    modifier_id = "modifier_id"
    modifier_date = "modifier_date"
    modifier_datetime = "modifier_datetime"
    modifier_concept_id = "modifier_concept_id"
    modifier_source_table = "modifier_source_table"
    target_event_id = "target_event_id"
    target_field_concept_id = "target_field_concept_id"
    value_as_number = "value_as_number"
    value_as_concept_id = "value_as_concept_id"
    unit_concept_id = "unit_concept_id"
    value_as_string = "value_as_string"

# we store this column listing because we want to be able to use Unions, 
# which rely on column ordering by role
CANONICAL_MODIFIER_REQUIRED_COLUMNS: tuple[ModifierColumn, ...] = (
    ModifierColumn.person_id,
    ModifierColumn.modifier_id,
    ModifierColumn.modifier_date,
    ModifierColumn.modifier_datetime,
    ModifierColumn.modifier_concept_id,
    ModifierColumn.modifier_source_table,
    ModifierColumn.target_event_id,
    ModifierColumn.target_field_concept_id,
)


CANONICAL_MODIFIER_VALUE_COLUMNS: tuple[ModifierColumn, ...] = (
    ModifierColumn.value_as_number,
    ModifierColumn.value_as_concept_id,
    ModifierColumn.unit_concept_id,
    ModifierColumn.value_as_string,
)


@runtime_checkable
class ModifierRow(Protocol):
    """Structural typing contract for the canonical identity and clinical row."""

    person_id: int
    modifier_id: int
    modifier_date: date
    modifier_datetime: datetime | None
    modifier_concept_id: int
    modifier_source_table: str
    target_event_id: int | None
    target_field_concept_id: int | None


@runtime_checkable
class ValuedModifierRow(ModifierRow, Protocol):
    """Canonical modifier row extended with all nullable value representations."""

    value_as_number: float | None
    value_as_concept_id: int | None
    unit_concept_id: int | None
    value_as_string: str | None


@dataclass(frozen=True, order=True, slots=True)
class ModifierIdentity:
    """Source-table-scoped identity of the modifier row itself."""

    modifier_source_table: str
    modifier_id: int

    def __post_init__(self) -> None:
        if not self.modifier_source_table.strip():
            raise ValueError("modifier_source_table must not be empty")


@dataclass(frozen=True, order=True, slots=True)
class ModifierTargetIdentity:
    """Field-concept-scoped identity of the row being modified."""

    target_field_concept_id: int
    target_event_id: int


class ModifierSelectionPolicy(StrEnum):
    """Supported temporal directions after any caller-supplied priority."""

    earliest = "earliest"
    latest = "latest"


@dataclass(frozen=True, slots=True)
class ModifierSelectionSpec:
    """Deterministic selection policy within a modifier target partition."""

    policy: ModifierSelectionPolicy = ModifierSelectionPolicy.earliest
    partition_by: tuple[str, ...] = (
        str(ModifierColumn.person_id),
        str(ModifierColumn.target_field_concept_id),
        str(ModifierColumn.target_event_id),
    )
    date_column: str = str(ModifierColumn.modifier_date)
    datetime_column: str = str(ModifierColumn.modifier_datetime)
    stable_identity_columns: tuple[str, ...] = (
        str(ModifierColumn.modifier_source_table),
        str(ModifierColumn.modifier_id),
    )

    def __post_init__(self) -> None:
        names = (
            *self.partition_by,
            self.date_column,
            self.datetime_column,
            *self.stable_identity_columns,
        )
        if not self.partition_by or not self.stable_identity_columns:
            raise ValueError("partition and stable identity columns must not be empty")
        if any(not name.strip() for name in names):
            raise ValueError("selection column names must not be empty")
        if len(set(self.partition_by)) != len(self.partition_by):
            raise ValueError("partition_by columns must be unique")


class ModifierTargetDiagnosticCode(StrEnum):
    """Reasons a canonical modifier could not resolve to its supplied target."""

    missing_target_identity = "missing_target_identity"
    unsupported_target_field = "unsupported_target_field"
    missing_target_event = "missing_target_event"
    person_mismatch = "person_mismatch"


class ModifierTargetDiagnosticColumn(StrEnum):
    """Stable output labels for advisory target-resolution diagnostics."""

    diagnostic_code = "diagnostic_code"
    modifier_source_table = "modifier_source_table"
    modifier_id = "modifier_id"
    target_field_concept_id = "target_field_concept_id"
    target_event_id = "target_event_id"
    message = "message"


@dataclass(frozen=True, slots=True)
class ModifierTargetDiagnostic:
    """Typed value representation of one target-resolution diagnostic row."""

    diagnostic_code: ModifierTargetDiagnosticCode
    modifier_source_table: str
    modifier_id: int
    target_field_concept_id: int | None
    target_event_id: int | None
    message: str

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> ModifierTargetDiagnostic:
        return cls(
            diagnostic_code=ModifierTargetDiagnosticCode(row["diagnostic_code"]),
            modifier_source_table=str(row["modifier_source_table"]),
            modifier_id=int(row["modifier_id"]),
            target_field_concept_id=row["target_field_concept_id"],
            target_event_id=row["target_event_id"],
            message=str(row["message"]),
        )
