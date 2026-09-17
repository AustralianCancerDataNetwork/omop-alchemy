import sqlalchemy as sa
import sqlalchemy.orm as so
from typing import Optional, TYPE_CHECKING
from datetime import date, datetime

from orm_loader.helpers import Base

from omop_alchemy.cdm.base import (
    CDMTableBase,
    cdm_table, 
    optional_concept_fk,
    PersonScoped,
    HealthSystemContext,
    required_concept_fk,
    optional_int,
    ReferenceContext,
    DomainValidationMixin,
    ExpectedDomain,
    merge_table_args,
    omop_index,
)

if TYPE_CHECKING:
    from ..vocabulary import Concept
    from ..clinical import Person
    from ..health_system import Provider, Visit_Occurrence, Visit_Detail

@cdm_table
class Note(CDMTableBase, Base, PersonScoped, HealthSystemContext):
    __tablename__ = "note"
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "person_id", cluster=True),
        omop_index(__tablename__, "note_type_concept_id"),
        omop_index(__tablename__, "visit_occurrence_id"),
    )
    note_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    note_date: so.Mapped[date] = so.mapped_column(sa.Date, nullable=False)
    note_datetime: so.Mapped[Optional[datetime]] = so.mapped_column(sa.DateTime,nullable=True,)
    note_type_concept_id: so.Mapped[int] = required_concept_fk()
    note_class_concept_id: so.Mapped[int] = required_concept_fk()
    note_title: so.Mapped[Optional[str]] = so.mapped_column(sa.String(250),nullable=True)
    note_text: so.Mapped[str] = so.mapped_column(sa.Text,nullable=False,)
    encoding_concept_id: so.Mapped[int] = required_concept_fk()
    language_concept_id: so.Mapped[int] = required_concept_fk()
    note_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50),nullable=True,)
    note_event_id: so.Mapped[Optional[int]] = optional_int()
    note_event_field_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()

    def __repr__(self) -> str:
        return f"<Note {self.note_id}>"


class NoteContext(ReferenceContext):
    person: so.Mapped["Person"] = ReferenceContext._reference_relationship(
        target="Person",
        local_fk="person_id",
    )  # type: ignore[assignment]

    note_type_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(
        target="Concept",
        local_fk="note_type_concept_id",
    )  # type: ignore[assignment]

    note_class_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(
        target="Concept",
        local_fk="note_class_concept_id",
    )  # type: ignore[assignment]

    encoding_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(
        target="Concept",
        local_fk="encoding_concept_id",
    )  # type: ignore[assignment]

    language_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(
        target="Concept",
        local_fk="language_concept_id",
    )  # type: ignore[assignment]

    provider: so.Mapped[Optional["Provider"]] = ReferenceContext._reference_relationship(
        target="Provider",
        local_fk="provider_id",
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

    note_event_field: so.Mapped[Optional["Concept"]] = (
        ReferenceContext._reference_relationship(
            target="Concept",
            local_fk="note_event_field_concept_id",
        )
    )  # type: ignore[assignment]

class NoteView(Note, NoteContext, DomainValidationMixin):

    __tablename__ = "note"
    __mapper_args__ = {"concrete": False}

    __expected_domains__ = {
        "note_type_concept_id": ExpectedDomain("Type Concept"),
        "note_class_concept_id": ExpectedDomain("Meas Value"),
    }

    @property
    def recorded_at(self):
        return self.note_datetime or self.note_date

    def __repr__(self) -> str:
        return f"<Note {self.note_id} ({self.note_date})>"
