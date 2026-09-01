import sqlalchemy as sa
import sqlalchemy.orm as so
from typing import Optional
from datetime import date

from oa_configurator import Role
from orm_loader.helpers import Base
from orm_loader.registry import ValidationIssue
from omop_alchemy.cdm.base import (
    ReferenceTable,
    cdm_table,
    CDMTableBase,
    DatedEvent,
    merge_table_args,
    omop_index,
    role_fk,
)
from omop_alchemy.cdm.model.flags import InvalidReasonMixin

@cdm_table
class Source_To_Concept_Map(
    DatedEvent,
    CDMTableBase,
    ReferenceTable,
    InvalidReasonMixin,
    Base,
):
    """
    Maps source codes to standard OMOP concepts.

    This table captures ETL provenance and is critical
    for reproducibility and transparency.
    """
    __tablename__ = "source_to_concept_map"
    __cdm_extra_checks__ = ["source_concept_id_range"]
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "target_concept_id", cluster=True),
        omop_index(__tablename__, "source_vocabulary_id"),
        omop_index(__tablename__, "target_vocabulary_id"),
        omop_index(__tablename__, "source_code"),
        {"schema": Role.VOCAB.value},
    )

    source_code: so.Mapped[str] = so.mapped_column(sa.String(50),primary_key=True)
    source_concept_id: so.Mapped[int] = so.mapped_column(sa.Integer,primary_key=True,doc="0 or >= 2,000,000,000 for site-specific concepts")
    source_vocabulary_id: so.Mapped[str] = so.mapped_column(sa.String(20),primary_key=True)
    source_code_description: so.Mapped[Optional[str]] = so.mapped_column(sa.String(255), nullable=True)
    target_concept_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")), nullable=False)
    target_vocabulary_id: so.Mapped[str] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "vocabulary.vocabulary_id")),nullable=False)
    valid_start_date: so.Mapped[date] = so.mapped_column(nullable=False)
    valid_end_date: so.Mapped[date] = so.mapped_column(nullable=False)

    @classmethod
    def extra_validate(cls) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        # Only structural; value-level checks require DB scan -> handled by registry semantic pass
        return issues
