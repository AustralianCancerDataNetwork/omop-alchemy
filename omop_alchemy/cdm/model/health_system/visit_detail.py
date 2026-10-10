import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role
from typing import Optional
from datetime import date

from orm_loader.helpers import Base 
from omop_alchemy.cdm.base import (
    role_fk,
    cdm_table,
    CDMTableBase,
    required_concept_fk,
    optional_concept_fk,
    merge_table_args,
    omop_index,
)

@cdm_table
class Visit_Detail(CDMTableBase, Base):
    __tablename__ = "visit_detail"
    __table_args__ = merge_table_args(
        {"schema": Role.PRIMARY.value},
        omop_index(__tablename__, "person_id", cluster=True),
        omop_index(__tablename__, "visit_detail_concept_id"),
        omop_index(__tablename__, "visit_occurrence_id")
    )

    visit_detail_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    person_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(role_fk(Role.PRIMARY, "person.person_id")), nullable=False)
    visit_detail_concept_id: so.Mapped[int] = required_concept_fk()
    visit_detail_start_date: so.Mapped[date] = so.mapped_column(sa.Date, nullable=False)
    visit_detail_start_datetime: so.Mapped[Optional[date]] = so.mapped_column(sa.DateTime, nullable=True)
    visit_detail_end_date: so.Mapped[date] = so.mapped_column(sa.Date, nullable=False)
    visit_detail_end_datetime: so.Mapped[Optional[date]] = so.mapped_column(sa.DateTime, nullable=True)
    visit_detail_type_concept_id: so.Mapped[int] = required_concept_fk()
    provider_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey(role_fk(Role.PRIMARY, "provider.provider_id")), nullable=True)
    care_site_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey(role_fk(Role.PRIMARY, "care_site.care_site_id")), nullable=True)
    visit_detail_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)
    visit_detail_source_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    admitted_from_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    admitted_from_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)
    discharged_to_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    discharged_to_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)
    preceding_visit_detail_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey(role_fk(Role.PRIMARY, "visit_detail.visit_detail_id")), nullable=True)
    parent_visit_detail_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey(role_fk(Role.PRIMARY, "visit_detail.visit_detail_id")), nullable=True)
    visit_occurrence_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(role_fk(Role.PRIMARY, "visit_occurrence.visit_occurrence_id")), nullable=False)

    def __repr__(self) -> str:
        return f"<VisitDetail {self.visit_detail_id}>"
