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
class Drug_Strength(
    CDMTableBase,
    ReferenceTable,
    InvalidReasonMixin,
    Base,
):
    """
    Defines the strength and composition of drug products.

    This table links drug products to their ingredients
    and quantitative properties.
    """
    __tablename__ = "drug_strength"
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "drug_concept_id", cluster=True),
        omop_index(__tablename__, "ingredient_concept_id"),
    )

    drug_concept_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey("concept.concept_id"),primary_key=True)
    ingredient_concept_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey("concept.concept_id"),primary_key=True)
    amount_value: so.Mapped[Optional[float]] = so.mapped_column(sa.Float, nullable=True)
    amount_unit_concept_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey("concept.concept_id"), nullable=True)
    numerator_value: so.Mapped[Optional[float]] = so.mapped_column(sa.Float, nullable=True)
    numerator_unit_concept_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey("concept.concept_id"), nullable=True)
    denominator_value: so.Mapped[Optional[float]] = so.mapped_column(sa.Float, nullable=True)
    denominator_unit_concept_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey("concept.concept_id"), nullable=True)
    box_size: so.Mapped[Optional[int]] = so.mapped_column(sa.Integer, nullable=True)
    valid_start_date: so.Mapped[date] = so.mapped_column(nullable=False)
    valid_end_date: so.Mapped[date] = so.mapped_column(nullable=False)
