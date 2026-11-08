from datetime import date

import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role
from orm_loader.helpers import Base

from omop_alchemy.cdm.base import (
    CDMTableBase,
    ValueMixin,
    cdm_table,
    merge_table_args,
    omop_index,
    required_concept_fk,
)


@cdm_table
class Metadata(CDMTableBase, Base, ValueMixin):
    __tablename__ = "metadata"
    __table_args__ = merge_table_args(
        {"schema": Role.PRIMARY.value},
        omop_index(__tablename__, "metadata_concept_id", cluster=True),
        omop_index(__tablename__, "metadata_type_concept_id"),
    )

    metadata_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    metadata_concept_id: so.Mapped[int] = required_concept_fk()
    metadata_type_concept_id: so.Mapped[int] = required_concept_fk()
    name: so.Mapped[str] = so.mapped_column(sa.String(250), nullable=False)
    value_as_string: so.Mapped[str | None] = so.mapped_column(sa.String(250), nullable=True)
    metadata_date: so.Mapped[date | None] = so.mapped_column(sa.Date, nullable=True)
    metadata_datetime: so.Mapped[date | None] = so.mapped_column(sa.DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<Metadata {self.metadata_id}: {self.name}>"
