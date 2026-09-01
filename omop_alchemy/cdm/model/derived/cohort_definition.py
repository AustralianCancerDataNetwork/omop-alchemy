import sqlalchemy as sa
import sqlalchemy.orm as so
from typing import Optional
from datetime import date
from oa_configurator import Role
from orm_loader.helpers import Base
from omop_alchemy.cdm.base import (
    cdm_table,
    CDMTableBase,
    merge_table_args,
    omop_index,
    role_fk,
)

@cdm_table
class Cohort_Definition(CDMTableBase, Base):
    __tablename__ = "cohort_definition"
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "definition_type_concept_id"),
        omop_index(__tablename__, "subject_concept_id"),
        {"schema": Role.RESULTS.value},
    )

    cohort_definition_id: so.Mapped[int] = so.mapped_column(primary_key=True)

    cohort_definition_name: so.Mapped[str] = so.mapped_column(sa.String(255), nullable=False)
    cohort_definition_description: so.Mapped[Optional[str]] = so.mapped_column(sa.Text, nullable=True)
    definition_type_concept_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")), nullable=False)
    cohort_definition_syntax: so.Mapped[Optional[str]] = so.mapped_column(sa.Text, nullable=True)
    subject_concept_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")), nullable=False)

    cohort_initiation_date: so.Mapped[Optional[date]] = so.mapped_column(sa.Date, nullable=True)

    def __repr__(self) -> str:
        return f"<CohortDefinition {self.cohort_definition_id}>"
