import sqlalchemy as sa
import sqlalchemy.orm as so
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
class Observation_Period(CDMTableBase, Base):
    __tablename__ = "observation_period"
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "person_id", cluster=True),
        omop_index(__tablename__, "period_type_concept_id"),
        {"schema": Role.RESULTS.value},
    )

    observation_period_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    person_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey("person.person_id"), nullable=False)
    observation_period_start_date: so.Mapped[date] = so.mapped_column(nullable=False)
    observation_period_end_date: so.Mapped[date] = so.mapped_column(nullable=False)
    period_type_concept_id: so.Mapped[int] = required_concept_fk()
