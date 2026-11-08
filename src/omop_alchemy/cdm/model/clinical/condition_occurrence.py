from datetime import date, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Optional, Self

import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role
from orm_loader.helpers import Base
from sqlalchemy.ext.declarative import declared_attr

from omop_alchemy.cdm.base import (
    CDMTableBase,
    ClinicalEventMixin,
    FactTable,
    HealthSystemContext,
    ModifierFieldConcepts,
    PersonScoped,
    ReferenceContext,
    cdm_table,
    merge_table_args,
    omop_index,
    optional_concept_fk,
    role_fk,
)

if TYPE_CHECKING:
    from ..health_system import Visit_Occurrence
    from ..vocabulary import Concept


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
        {"schema": Role.PRIMARY.value},
        omop_index(__tablename__, "person_id", cluster=True),
        omop_index(__tablename__, "condition_concept_id"),
        omop_index(__tablename__, "visit_occurrence_id"),
    )
    condition_occurrence_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    condition_concept_id: so.Mapped[int] = so.mapped_column(
        sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")), nullable=False
    )
    condition_start_date: so.Mapped[date] = so.mapped_column(nullable=False)
    condition_start_datetime: so.Mapped[datetime | None] = so.mapped_column()
    condition_end_date: so.Mapped[date | None] = so.mapped_column()
    condition_end_datetime: so.Mapped[datetime | None] = so.mapped_column()
    condition_type_concept_id: so.Mapped[int] = so.mapped_column(
        sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")), nullable=False
    )
    stop_reason: so.Mapped[str | None] = so.mapped_column(sa.String(20))
    condition_source_value: so.Mapped[str | None] = so.mapped_column(sa.String(50))
    condition_status_source_value: so.Mapped[str | None] = so.mapped_column(sa.String(50))
    condition_source_concept_id: so.Mapped[int | None] = optional_concept_fk()
    condition_status_concept_id: so.Mapped[int | None] = optional_concept_fk()


class Condition_OccurrenceContext(ReferenceContext):
    condition_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(
        target="Concept", local_fk="condition_concept_id"
    )
    condition_type: so.Mapped["Concept"] = ReferenceContext._reference_relationship(
        target="Concept", local_fk="condition_type_concept_id"
    )
    condition_source_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(
        target="Concept", local_fk="condition_source_concept_id"
    )
    condition_status: so.Mapped["Concept"] = ReferenceContext._reference_relationship(
        target="Concept", local_fk="condition_status_concept_id"
    )

    @declared_attr
    def visit_occurrence(cls: type[Self]) -> so.Mapped[Optional["Visit_Occurrence"]]:
        return so.relationship(
            "Visit_Occurrence",
            primaryjoin=f"{cls.__name__}.visit_occurrence_id == Visit_Occurrence.visit_occurrence_id",
            foreign_keys=f"{cls.__name__}.visit_occurrence_id",
            viewonly=True,
            lazy="selectin",
        )


class Condition_OccurrenceView(
    Condition_Occurrence, Condition_OccurrenceContext, ClinicalEventMixin
):
    __tablename__ = "condition_occurrence"
    # Must match Condition_Occurrence's schema, or SQLAlchemy silently builds a second, unlinked Table object.
    __table_args__ = ({"schema": Role.PRIMARY.value},)
    __mapper_args__ = MappingProxyType({"concrete": False})
    __event_id_col__ = "condition_occurrence_id"
    __concept_id_col__ = "condition_concept_id"
    __start_date_col__ = "condition_start_date"
    __end_date_col__ = "condition_end_date"
    __type_concept_id_col__ = "condition_type_concept_id"

    @classmethod
    def modifier_field_concept_id(cls) -> int:
        return ModifierFieldConcepts.CONDITION_OCCURRENCE
