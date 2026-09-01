import sqlalchemy as sa
import sqlalchemy.orm as so
from typing import Optional
from datetime import date
from oa_configurator import Role
from orm_loader.helpers import Base
from omop_alchemy.cdm.base import (
    cdm_table,
    CDMTableBase,
    required_concept_fk,
    merge_table_args,
    omop_index,
)

@cdm_table
class Condition_Era(CDMTableBase, Base):
    __tablename__ = "condition_era"
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "person_id", cluster=True),
        omop_index(__tablename__, "condition_concept_id"),
        {"schema": Role.RESULTS.value},
    )

    condition_era_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    person_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey("person.person_id"), nullable=False)
    condition_concept_id: so.Mapped[int] = required_concept_fk()
    condition_era_start_date: so.Mapped[date] = so.mapped_column(nullable=False)
    condition_era_end_date: so.Mapped[date] = so.mapped_column(nullable=False)
    condition_occurrence_count: so.Mapped[Optional[int]] = so.mapped_column(nullable=True)
