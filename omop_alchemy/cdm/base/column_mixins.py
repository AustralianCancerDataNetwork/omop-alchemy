from __future__ import annotations

from datetime import date, datetime
from typing import Optional, Any
import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role

from .column_helpers import role_fk


"""
OMOP CDM structural mixins.

These mixins encode *structural semantics* of the OMOP Common Data Model
as defined in the Table-Level and Field-Level CSV specifications.

They intentionally do NOT encode clinical or analytical meaning.
"""

class PersonScoped:
    """
    `PersonScoped`

    Used for tables that are fundamentally scoped to a person.

    Encodes the standard `person_id` foreign key and indexing pattern.
    """
    person_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey("person.person_id"), nullable=False)

class ConceptTyped:
    """
    `ConceptTyped`

    Mixin for tables whose primary meaning is encoded by a concept_id.
    
    Subclasses MUST define *something_concept_id*.

    Not currently used, but intention to build out logic for generic EAV queries & timeline projections.
    """

    @so.declared_attr
    def concept_id(cls: Any) -> so.Mapped[int]:
        raise NotImplementedError(
            f"{cls.__name__} must define its own <x>_concept_id column"
        )

class ValueMixin:
    """
    `ValueMixin`

    Encodes the OMOP pattern where a value may be represented *either numerically or categorically*,
    via `value_as_number` and/or `value_as_concept_id`.

    This mixin only supplies the two columns. It does NOT enforce that either is
    populated: OMOP CDM v5.4 does not mandate a value on every row for every
    table this mixin is used by (e.g. `Measurement`, `Metadata`), and some
    tables represent a third value form (`Observation.value_as_string`) that
    this mixin has no way to know about. Subclasses that need a "some value
    must be present" guarantee should declare their own check constraint and
    `@validates` covering exactly the value columns they actually have.

    This helps when building generic tooling that needs to handle values flexibly but then normalise for analysis.
    """
    value_as_number: so.Mapped[Optional[float]] = so.mapped_column(sa.Float, nullable=True)
    value_as_concept_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")), nullable=True)

class DatedEvent:
    """
    `DatedEvent`

    Mixin for tables with start/end date and datetime pairs.

    This mixin does **not** enforce temporal logic (e.g. start ≤ end); it only defines the shape,
    but this may be integrated with the event timeline machinery in future.

    """
    start_date: so.Mapped[date] = so.mapped_column(nullable=False)
    start_datetime: so.Mapped[Optional[datetime]] = so.mapped_column(nullable=True)

    end_date: so.Mapped[Optional[date]] = so.mapped_column(nullable=True)
    end_datetime: so.Mapped[Optional[datetime]] = so.mapped_column(nullable=True)


class HealthSystemContext:
    """
    `HealthSystemContext`

    Encodes attribution to providers and visits.

    Used across many clinical event tables to provide consistent join points into the health system structure.
    """
    provider_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey("provider.provider_id"), nullable=True)
    visit_occurrence_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey("visit_occurrence.visit_occurrence_id"), nullable=True)
    visit_detail_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey("visit_detail.visit_detail_id"), nullable=True)

class FactTable:
    """
    `FactTable`

    Marker mixin for OMOP fact tables.

    Used for introspection, tooling, and documentation only.
    """
    pass

class ReferenceTable:
    """
    `ReferenceTable`

    Marker mixin for OMOP reference tables.
    """
    pass

class SourceAttribution:
    """
    `SourceAttribution`

    Mixin for *_source_value and *_source_concept_id patterns.
    """
    source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String, nullable=True)
    source_concept_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")), nullable=True)

class UnitConcept:
    """
    `UnitConcept`
    
    Mixin for unit_concept_id.
    """
    unit_concept_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")), nullable=True)
