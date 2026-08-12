from __future__ import annotations
import sqlalchemy as sa
import sqlalchemy.orm as so
from typing import Optional, TYPE_CHECKING
from datetime import date
from orm_loader.helpers import Base
from omop_alchemy.cdm.base import (
    cdm_table,
    CDMTableBase,
    optional_concept_fk,
    required_concept_fk,
    PersonScoped,
    optional_int,
    HealthSystemContext,
    ReferenceContext,
    DomainValidationMixin,
    ExpectedDomain,
    ModifierTargetMixin,
    ModifierFieldConcepts,
    merge_table_args,
    omop_index,
)
if TYPE_CHECKING:
    from ..vocabulary import Concept
    from ..clinical import Person
    from ..health_system import Provider, Visit_Occurrence, Visit_Detail

@cdm_table
class Procedure_Occurrence(CDMTableBase, Base, PersonScoped, HealthSystemContext):
    __tablename__ = "procedure_occurrence"
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "person_id", cluster=True),
        omop_index(__tablename__, "procedure_concept_id"),
        omop_index(__tablename__, "visit_occurrence_id")
    )
    procedure_occurrence_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    procedure_concept_id: so.Mapped[int] = required_concept_fk()
    procedure_date: so.Mapped[date] = so.mapped_column(sa.Date, nullable=False)
    procedure_datetime: so.Mapped[Optional[date]] = so.mapped_column(sa.DateTime,nullable=True,)
    procedure_end_date: so.Mapped[Optional[date]] = so.mapped_column(sa.Date,nullable=True,)
    procedure_end_datetime: so.Mapped[Optional[date]] = so.mapped_column(sa.DateTime,nullable=True,)
    procedure_type_concept_id: so.Mapped[int] = required_concept_fk()
    modifier_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    quantity: so.Mapped[Optional[int]] = optional_int()
    procedure_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50),nullable=True,)
    procedure_source_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()

    modifier_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50),nullable=True,)

    def __repr__(self) -> str:
        return f"<Procedure_Occurrence {self.procedure_occurrence_id}>"


class Procedure_OccurrenceContext(ReferenceContext):
    person: so.Mapped["Person"] = ReferenceContext._reference_relationship(
        target="Person",
        local_fk="person_id",
        remote_pk="person_id",
    )  # type: ignore[assignment]

    procedure_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(
        target="Concept",
        local_fk="procedure_concept_id",
        remote_pk="concept_id",
    )  # type: ignore[assignment]

    procedure_type_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(
        target="Concept",
        local_fk="procedure_type_concept_id",
        remote_pk="concept_id",
    )  # type: ignore[assignment]

    modifier_concept: so.Mapped[Optional["Concept"]] = (
        ReferenceContext._reference_relationship(
            target="Concept",
            local_fk="modifier_concept_id",
            remote_pk="concept_id",
        )
    )  # type: ignore[assignment]

    provider: so.Mapped[Optional["Provider"]] = ReferenceContext._reference_relationship(
        target="Provider",
        local_fk="provider_id",
        remote_pk="provider_id",
    )  # type: ignore[assignment]

    visit_occurrence: so.Mapped[Optional["Visit_Occurrence"]] = (
        ReferenceContext._reference_relationship(
            target="Visit_Occurrence",
            local_fk="visit_occurrence_id",
            remote_pk="visit_occurrence_id",
        )
    )  # type: ignore[assignment]

    visit_detail: so.Mapped[Optional["Visit_Detail"]] = (
        ReferenceContext._reference_relationship(
            target="Visit_Detail",
            local_fk="visit_detail_id",
            remote_pk="visit_detail_id",
        )
    )  # type: ignore[assignment]

    procedure_source_concept: so.Mapped[Optional["Concept"]] = (
        ReferenceContext._reference_relationship(
            target="Concept",
            local_fk="procedure_source_concept_id",
            remote_pk="concept_id",
        )
    )  # type: ignore[assignment]


class Procedure_OccurrenceView(
    Procedure_Occurrence,
    Procedure_OccurrenceContext,
    DomainValidationMixin,
    ModifierTargetMixin,
):
    __tablename__ = "procedure_occurrence"
    __mapper_args__ = {"concrete": False}

    __event_id_col__ = "procedure_occurrence_id"
    __concept_id_col__ = "procedure_concept_id"
    __start_date_col__ = "procedure_date"
    __end_date_col__ = "procedure_end_date"
    __type_concept_id_col__ = "procedure_type_concept_id"

    __expected_domains__ = {
        "procedure_concept_id": ExpectedDomain("Procedure"),
        "procedure_type_concept_id": ExpectedDomain("Type Concept"),
    }

    @property
    def start(self):
        """Best-effort start datetime."""
        return self.procedure_datetime or self.procedure_date

    @classmethod
    def modifier_field_concept_id(cls) -> int:
        return ModifierFieldConcepts.PROCEDURE_OCCURRENCE

    @property
    def end(self):
        """Best-effort end datetime."""
        return (
            self.procedure_end_datetime
            or self.procedure_end_date
            or self.start
        )

    def __repr__(self) -> str:
        return (
            f"<Procedure_Occurrence {self.procedure_occurrence_id}: "
            f"{self.procedure_concept_id} "
            f"({self.procedure_date})>"
        )
