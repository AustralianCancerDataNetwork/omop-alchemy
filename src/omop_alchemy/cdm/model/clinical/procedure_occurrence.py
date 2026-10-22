from __future__ import annotations

from datetime import date
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar

import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role
from orm_loader.helpers import Base

from omop_alchemy.cdm.base import (
    CDMTableBase,
    ClinicalEventMixin,
    DomainValidationMixin,
    ExpectedDomain,
    HealthSystemContext,
    ModifierFieldConcepts,
    PersonScoped,
    ReferenceContext,
    cdm_table,
    merge_table_args,
    omop_index,
    optional_concept_fk,
    optional_int,
    required_concept_fk,
)

if TYPE_CHECKING:
    from ..clinical import Person
    from ..health_system import Provider, Visit_Detail, Visit_Occurrence
    from ..vocabulary import Concept


@cdm_table
class Procedure_Occurrence(CDMTableBase, Base, PersonScoped, HealthSystemContext):
    __tablename__ = "procedure_occurrence"
    __table_args__ = merge_table_args(
        {"schema": Role.PRIMARY.value},
        omop_index(__tablename__, "person_id", cluster=True),
        omop_index(__tablename__, "procedure_concept_id"),
        omop_index(__tablename__, "visit_occurrence_id"),
    )
    procedure_occurrence_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    procedure_concept_id: so.Mapped[int] = required_concept_fk()
    procedure_date: so.Mapped[date] = so.mapped_column(sa.Date, nullable=False)
    procedure_datetime: so.Mapped[date | None] = so.mapped_column(
        sa.DateTime,
        nullable=True,
    )
    procedure_end_date: so.Mapped[date | None] = so.mapped_column(
        sa.Date,
        nullable=True,
    )
    procedure_end_datetime: so.Mapped[date | None] = so.mapped_column(
        sa.DateTime,
        nullable=True,
    )
    procedure_type_concept_id: so.Mapped[int] = required_concept_fk()
    modifier_concept_id: so.Mapped[int | None] = optional_concept_fk()
    quantity: so.Mapped[int | None] = optional_int()
    procedure_source_value: so.Mapped[str | None] = so.mapped_column(
        sa.String(50),
        nullable=True,
    )
    procedure_source_concept_id: so.Mapped[int | None] = optional_concept_fk()

    modifier_source_value: so.Mapped[str | None] = so.mapped_column(
        sa.String(50),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<Procedure_Occurrence {self.procedure_occurrence_id}>"


class Procedure_OccurrenceContext(ReferenceContext):
    person: so.Mapped[Person] = ReferenceContext._reference_relationship(
        target="Person",
        local_fk="person_id",
    )  # type: ignore[assignment]

    procedure_concept: so.Mapped[Concept] = ReferenceContext._reference_relationship(
        target="Concept",
        local_fk="procedure_concept_id",
    )  # type: ignore[assignment]

    procedure_type_concept: so.Mapped[Concept] = ReferenceContext._reference_relationship(
        target="Concept",
        local_fk="procedure_type_concept_id",
    )  # type: ignore[assignment]

    modifier_concept: so.Mapped[Concept | None] = ReferenceContext._reference_relationship(
        target="Concept",
        local_fk="modifier_concept_id",
    )  # type: ignore[assignment]

    provider: so.Mapped[Provider | None] = ReferenceContext._reference_relationship(
        target="Provider",
        local_fk="provider_id",
    )  # type: ignore[assignment]

    visit_occurrence: so.Mapped[Visit_Occurrence | None] = ReferenceContext._reference_relationship(
        target="Visit_Occurrence",
        local_fk="visit_occurrence_id",
    )  # type: ignore[assignment]

    visit_detail: so.Mapped[Visit_Detail | None] = ReferenceContext._reference_relationship(
        target="Visit_Detail",
        local_fk="visit_detail_id",
    )  # type: ignore[assignment]

    procedure_source_concept: so.Mapped[Concept | None] = ReferenceContext._reference_relationship(
        target="Concept",
        local_fk="procedure_source_concept_id",
    )  # type: ignore[assignment]


class Procedure_OccurrenceView(
    Procedure_Occurrence,
    Procedure_OccurrenceContext,
    DomainValidationMixin,
    ClinicalEventMixin,
):
    __tablename__ = "procedure_occurrence"
    # Must match Procedure_Occurrence's schema, or SQLAlchemy silently builds a second, unlinked Table object.
    __table_args__ = ({"schema": Role.PRIMARY.value},)
    __mapper_args__ = MappingProxyType({"concrete": False})

    __event_id_col__ = "procedure_occurrence_id"
    __concept_id_col__ = "procedure_concept_id"
    __start_date_col__ = "procedure_date"
    __end_date_col__ = "procedure_end_date"
    __type_concept_id_col__ = "procedure_type_concept_id"

    __expected_domains__: ClassVar[dict[str, ExpectedDomain]] = {
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
        return self.procedure_end_datetime or self.procedure_end_date or self.start

    def __repr__(self) -> str:
        return (
            f"<Procedure_Occurrence {self.procedure_occurrence_id}: "
            f"{self.procedure_concept_id} "
            f"({self.procedure_date})>"
        )
