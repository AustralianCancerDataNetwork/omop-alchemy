from __future__ import annotations

import sqlalchemy as sa
import sqlalchemy.orm as so
from typing import Optional, TYPE_CHECKING
from datetime import date, datetime
from oa_configurator import Role
from orm_loader.helpers import Base
from omop_alchemy.cdm.base import (
    CDMTableBase,
    DomainValidationMixin,
    ExpectedDomain,
    ModifierFieldConcepts,
    ModifierSourceMixin,
    ClinicalEventMixin,
    ReferenceContext,
    cdm_table,
    optional_concept_fk,
    role_fk,
    ValueMixin,
    merge_table_args,
    omop_index,
)

if TYPE_CHECKING:
    from ..health_system import Provider, Visit_Detail, Visit_Occurrence
    from ..vocabulary import Concept
    from .person import Person


@cdm_table
class Measurement(Base, CDMTableBase, ValueMixin, ModifierSourceMixin):
    __tablename__ = "measurement"
    __table_args__ = merge_table_args(
        {"schema": Role.PRIMARY.value},
        omop_index(__tablename__, "person_id", cluster=True),
        omop_index(__tablename__, "measurement_concept_id"),
        omop_index(__tablename__, "visit_occurrence_id"),
        # this one is not present in the CDM DDL but is needed for efficient querying of modifiers
        omop_index(__tablename__, "meas_event_field_concept_id"),
    )

    measurement_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    person_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(role_fk(Role.PRIMARY, "person.person_id")), nullable=False)
    measurement_concept_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")), nullable=False)
    measurement_date: so.Mapped[date] = so.mapped_column(nullable=False)
    measurement_datetime: so.Mapped[Optional[datetime]]
    measurement_time: so.Mapped[Optional[str]]
    measurement_type_concept_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")), nullable=False)
    operator_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    unit_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()

    range_low: so.Mapped[Optional[float]]
    range_high: so.Mapped[Optional[float]]

    provider_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey(role_fk(Role.PRIMARY, "provider.provider_id")))
    visit_occurrence_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey(role_fk(Role.PRIMARY, "visit_occurrence.visit_occurrence_id")))
    visit_detail_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey(role_fk(Role.PRIMARY, "visit_detail.visit_detail_id")))

    measurement_source_value: so.Mapped[Optional[str]]
    measurement_source_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    unit_source_value: so.Mapped[Optional[str]]
    unit_source_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()

    value_source_value: so.Mapped[Optional[str]]
    measurement_event_id: so.Mapped[Optional[int]]
    meas_event_field_concept_id: so.Mapped[Optional[int]] = optional_concept_fk(doc="Identifies which OMOP table measurement_event_id refers to")

    __modifier_event_id_col__ = "measurement_event_id"
    __modifier_field_concept_id_col__ = "meas_event_field_concept_id"


class MeasurementContext(ReferenceContext):
    """Read-only analytical relationships for a Measurement row."""

    person: so.Mapped["Person"] = ReferenceContext._reference_relationship(
        target="Person", local_fk="person_id"
    )  # type: ignore[assignment]
    measurement_concept: so.Mapped["Concept"] = (
        ReferenceContext._reference_relationship(
            target="Concept", local_fk="measurement_concept_id"
        )
    )  # type: ignore[assignment]
    measurement_type_concept: so.Mapped["Concept"] = (
        ReferenceContext._reference_relationship(
            target="Concept",
            local_fk="measurement_type_concept_id",
        )
    )  # type: ignore[assignment]
    unit_concept: so.Mapped[Optional["Concept"]] = (
        ReferenceContext._reference_relationship(
            target="Concept", local_fk="unit_concept_id"
        )
    )  # type: ignore[assignment]
    unit_source_concept: so.Mapped[Optional["Concept"]] = (
        ReferenceContext._reference_relationship(
            target="Concept",
            local_fk="unit_source_concept_id",
        )
    )  # type: ignore[assignment]
    provider: so.Mapped[Optional["Provider"]] = (
        ReferenceContext._reference_relationship(
            target="Provider", local_fk="provider_id"
        )
    )  # type: ignore[assignment]
    visit_occurrence: so.Mapped[Optional["Visit_Occurrence"]] = (
        ReferenceContext._reference_relationship(
            target="Visit_Occurrence",
            local_fk="visit_occurrence_id",
        )
    )  # type: ignore[assignment]
    visit_detail: so.Mapped[Optional["Visit_Detail"]] = (
        ReferenceContext._reference_relationship(
            target="Visit_Detail",
            local_fk="visit_detail_id",
        )
    )  # type: ignore[assignment]


class MeasurementView(
    Measurement,
    MeasurementContext,
    DomainValidationMixin,
    ClinicalEventMixin,
):
    """Analytical Measurement mapping with event metadata and reference context."""

    __tablename__ = "measurement"
    # Must match Measurement's schema, or SQLAlchemy silently builds a second, unlinked Table object.
    __table_args__ = {"schema": Role.PRIMARY.value}
    __mapper_args__ = {"concrete": False}
    __event_id_col__ = "measurement_id"
    __concept_id_col__ = "measurement_concept_id"
    __start_date_col__ = "measurement_date"
    # One date column: the target API aliases it; interval metadata treats
    # equal start/end declarations as a point with no independent endpoint.
    __end_date_col__ = "measurement_date"
    __type_concept_id_col__ = "measurement_type_concept_id"
    __expected_domains__ = {
        "measurement_concept_id": ExpectedDomain("Measurement"),
        "measurement_type_concept_id": ExpectedDomain("Type Concept"),
    }

    @classmethod
    def modifier_field_concept_id(cls) -> int:
        return ModifierFieldConcepts.MEASUREMENT
