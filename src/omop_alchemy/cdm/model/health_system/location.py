import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role
from typing import Optional
from orm_loader.helpers import Base

from omop_alchemy.cdm.base import (
    cdm_table,
    CDMTableBase,
    optional_concept_fk,
    merge_table_args,
    omop_index,
    omop_primary_key_index_name,
    omop_table_options,
)

@cdm_table
class Location(CDMTableBase, Base):
    __tablename__ = "location"
    __table_args__ = merge_table_args(
        {"schema": Role.PRIMARY.value},
        omop_index(__tablename__, "country_concept_id"),
        omop_table_options(cluster_on=omop_primary_key_index_name("location")),
    )
    location_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    address_1: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)
    address_2: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)
    city: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)
    state: so.Mapped[Optional[str]] = so.mapped_column(sa.String(2), nullable=True)
    zip: so.Mapped[Optional[str]] = so.mapped_column(sa.String(9), nullable=True)
    county: so.Mapped[Optional[str]] = so.mapped_column(sa.String(20), nullable=True)
    location_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)
    country_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    country_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(80), nullable=True)
    latitude: so.Mapped[Optional[float]] = so.mapped_column(sa.Float, nullable=True)
    longitude: so.Mapped[Optional[float]] = so.mapped_column(sa.Float, nullable=True)

    def __repr__(self) -> str:
        return f"<Location {self.location_id}>"
