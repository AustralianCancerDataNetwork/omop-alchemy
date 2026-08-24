import sqlalchemy as sa
import sqlalchemy.orm as so
from typing import Optional
from datetime import date
from orm_loader.helpers import Base
from omop_alchemy.cdm.base import (
    ReferenceTable,
    cdm_table,
    CDMTableBase,
    merge_table_args,
    omop_index,
)
from omop_alchemy.cdm.model.flags import InvalidReasonMixin

@cdm_table
class Concept_Relationship(
    ReferenceTable, 
    CDMTableBase, 
    InvalidReasonMixin, 
    Base
    ):
    __tablename__ = "concept_relationship"
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "concept_id_1", cluster=True),
        omop_index(__tablename__, "concept_id_2"),
        omop_index(__tablename__, "relationship_id"),
    )
    concept_id_1: so.Mapped[int] = so.mapped_column(sa.ForeignKey("concept.concept_id"),primary_key=True)
    concept_id_2: so.Mapped[int] = so.mapped_column(sa.ForeignKey("concept.concept_id"),primary_key=True)
    relationship_id: so.Mapped[str] = so.mapped_column(sa.ForeignKey("relationship.relationship_id"),primary_key=True)
    valid_start_date: so.Mapped[date] = so.mapped_column(nullable=False)
    valid_end_date: so.Mapped[date] = so.mapped_column(nullable=False)
