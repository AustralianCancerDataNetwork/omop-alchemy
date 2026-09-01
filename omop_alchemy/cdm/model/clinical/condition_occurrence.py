import sqlalchemy as sa
import sqlalchemy.orm as so
from sqlalchemy.ext.declarative import declared_attr
from typing import Optional, TYPE_CHECKING
from datetime import date, datetime
from oa_configurator import Role
from orm_loader.helpers import Base
from omop_alchemy.cdm.base import (
    PersonScoped,
    HealthSystemContext,
    FactTable,
    ReferenceContext,
    CDMTableBase,
    cdm_table,
    ModifierFieldConcepts,
    ModifierTargetMixin,
    merge_table_args,
    omop_index,
    optional_concept_fk,
    role_fk,
)

if TYPE_CHECKING:
    from ..vocabulary import Concept
    from ..health_system import Visit_Occurrence

@cdm_table
class Condition_Occurrence(
    PersonScoped,
    HealthSystemContext,
    CDMTableBase,
    FactTable,
    Base,
):
    __tablename__ = "condition_occurrence"
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "person_id", cluster=True),
        omop_index(__tablename__, "condition_concept_id"),
        omop_index(__tablename__, "visit_occurrence_id")
    )
    condition_occurrence_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    condition_concept_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")), nullable=False)
    condition_start_date: so.Mapped[date] = so.mapped_column(nullable=False)
    condition_start_datetime: so.Mapped[Optional[datetime]] = so.mapped_column()
    condition_end_date: so.Mapped[Optional[date]] = so.mapped_column()
    condition_end_datetime: so.Mapped[Optional[datetime]] = so.mapped_column()
    condition_type_concept_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")), nullable=False)
    stop_reason: so.Mapped[Optional[str]] = so.mapped_column(sa.String(20))
    condition_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50))
    condition_status_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50))
    condition_source_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    condition_status_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()

class Condition_OccurrenceContext(ReferenceContext):
    condition_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(target="Concept", local_fk="condition_concept_id", remote_pk="concept_id")  # type: ignore[assignment]
    condition_type: so.Mapped["Concept"] = ReferenceContext._reference_relationship(target="Concept", local_fk="condition_type_concept_id", remote_pk="concept_id")  # type: ignore[assignment]
    condition_source_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(target="Concept", local_fk="condition_source_concept_id", remote_pk="concept_id")  # type: ignore[assignment]
    condition_status: so.Mapped["Concept"] = ReferenceContext._reference_relationship(target="Concept", local_fk="condition_status_concept_id", remote_pk="concept_id")  # type: ignore[assignment]

    @declared_attr
    def visit_occurrence(cls) -> so.Mapped[Optional["Visit_Occurrence"]]:
        return so.relationship(
            "Visit_Occurrence",
            primaryjoin=f"{cls.__name__}.visit_occurrence_id == Visit_Occurrence.visit_occurrence_id",  # type: ignore
            foreign_keys=f"{cls.__name__}.visit_occurrence_id", # type: ignore
            viewonly=True,
            lazy="selectin",
        )

class Condition_OccurrenceView(
    Condition_Occurrence, 
    Condition_OccurrenceContext, 
    ModifierTargetMixin
):
    __tablename__ = "condition_occurrence"
    __mapper_args__ = {"concrete": False}
    __event_id_col__ = "condition_occurrence_id"
    __concept_id_col__ = "condition_concept_id"
    __start_date_col__ = "condition_start_date"
    __end_date_col__ = "condition_end_date"
    __type_concept_id_col__ = "condition_type_concept_id"
    
    @classmethod
    def modifier_field_concept_id(cls) -> int:
        return ModifierFieldConcepts.CONDITION_OCCURRENCE
    
