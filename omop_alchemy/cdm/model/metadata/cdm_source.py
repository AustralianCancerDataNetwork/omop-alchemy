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
class CDM_Source(CDMTableBase, Base):
    __tablename__ = "cdm_source"
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "cdm_version_concept_id")
    )

    cdm_source_name: so.Mapped[str] = so.mapped_column(sa.String(255), nullable=False, primary_key=True)
    cdm_source_abbreviation: so.Mapped[str] = so.mapped_column(sa.String(25), nullable=False)
    cdm_holder: so.Mapped[str] = so.mapped_column(sa.String(255), nullable=False)

    source_description: so.Mapped[Optional[str]] = so.mapped_column(sa.Text, nullable=True)
    source_documentation_reference: so.Mapped[Optional[str]] = so.mapped_column(sa.String(255), nullable=True)
    cdm_etl_reference: so.Mapped[Optional[str]] = so.mapped_column(sa.String(255), nullable=True)

    source_release_date: so.Mapped[date] = so.mapped_column(sa.Date, nullable=False)
    cdm_release_date: so.Mapped[date] = so.mapped_column(sa.Date, nullable=False)

    cdm_version: so.Mapped[Optional[str]] = so.mapped_column(sa.String(10), nullable=True)
    cdm_version_concept_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")), nullable=False)

    vocabulary_version: so.Mapped[str] = so.mapped_column(sa.String(20), nullable=False)

    def __repr__(self) -> str:
        return f"<CDM_Source {self.cdm_source_abbreviation}>"
