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
class Drug_Era(CDMTableBase, Base):
    __tablename__ = "drug_era"
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "person_id", cluster=True),
        omop_index(__tablename__, "drug_concept_id"),
        {"schema": Role.RESULTS.value},
    )

    drug_era_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    person_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey("person.person_id"), nullable=False)
    drug_concept_id: so.Mapped[int] = required_concept_fk()
    drug_era_start_date: so.Mapped[date] = so.mapped_column(nullable=False)
    drug_era_end_date: so.Mapped[date] = so.mapped_column(nullable=False)
    drug_exposure_count: so.Mapped[Optional[int]] = so.mapped_column(nullable=True)
    gap_days: so.Mapped[Optional[int]] = so.mapped_column(nullable=True)
