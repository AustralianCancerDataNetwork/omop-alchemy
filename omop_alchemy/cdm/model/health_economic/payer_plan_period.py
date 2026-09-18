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
    optional_concept_fk,
    merge_table_args,
    omop_index,
)

@cdm_table
class Payer_Plan_Period(CDMTableBase, Base):
    __tablename__ = "payer_plan_period"
    __table_args__ = merge_table_args(
        {"schema": Role.PRIMARY.value},
        omop_index(__tablename__, "person_id", cluster=True),
    )

    payer_plan_period_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    person_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(role_fk(Role.PRIMARY, "person.person_id")), nullable=False)
    payer_plan_period_start_date: so.Mapped[date] = so.mapped_column(nullable=False)
    payer_plan_period_end_date: so.Mapped[date] = so.mapped_column(nullable=False)

    payer_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    payer_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)
    payer_source_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()

    plan_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    plan_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)
    plan_source_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()

    sponsor_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    sponsor_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)
    sponsor_source_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()

    family_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)

    stop_reason_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    stop_reason_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)
    stop_reason_source_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
