from datetime import date

import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role
from orm_loader.helpers import Base

from omop_alchemy.cdm.base import (
    CDMTableBase,
    cdm_table,
    merge_table_args,
    omop_index,
    optional_concept_fk,
    required_concept_fk,
    role_fk,
)


@cdm_table
class Specimen(CDMTableBase, Base):
    __tablename__ = "specimen"
    __table_args__ = merge_table_args(
        {"schema": Role.PRIMARY.value},
        omop_index(__tablename__, "person_id", cluster=True),
        omop_index(__tablename__, "specimen_concept_id"),
    )

    specimen_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    person_id: so.Mapped[int] = so.mapped_column(
        sa.ForeignKey(role_fk(Role.PRIMARY, "person.person_id")), nullable=False
    )

    specimen_concept_id: so.Mapped[int] = required_concept_fk()
    specimen_type_concept_id: so.Mapped[int] = required_concept_fk()

    specimen_date: so.Mapped[date] = so.mapped_column(nullable=False)
    specimen_datetime: so.Mapped[date | None] = so.mapped_column(sa.DateTime, nullable=True)

    quantity: so.Mapped[float | None] = so.mapped_column(nullable=True)
    unit_concept_id: so.Mapped[int | None] = optional_concept_fk()

    anatomic_site_concept_id: so.Mapped[int | None] = optional_concept_fk()
    disease_status_concept_id: so.Mapped[int | None] = optional_concept_fk()

    specimen_source_id: so.Mapped[str | None] = so.mapped_column(sa.String(50), nullable=True)
    specimen_source_value: so.Mapped[str | None] = so.mapped_column(sa.String(50), nullable=True)

    unit_source_value: so.Mapped[str | None] = so.mapped_column(sa.String(50), nullable=True)
    anatomic_site_source_value: so.Mapped[str | None] = so.mapped_column(
        sa.String(50), nullable=True
    )
    disease_status_source_value: so.Mapped[str | None] = so.mapped_column(
        sa.String(50), nullable=True
    )
