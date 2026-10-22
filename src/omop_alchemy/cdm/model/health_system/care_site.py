import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role
from typing import Optional

from orm_loader.helpers import Base
from omop_alchemy.cdm.base import (
    role_fk,
    cdm_table,
    CDMTableBase,
    optional_concept_fk,
    merge_table_args,
    omop_index,
    omop_primary_key_index_name,
    omop_table_options,
)

@cdm_table
class Care_Site(CDMTableBase, Base):
    __tablename__ = "care_site"
    __table_args__ = merge_table_args(
        {"schema": Role.PRIMARY.value},
        omop_index(__tablename__, "place_of_service_concept_id"),
        omop_index(__tablename__, "location_id"),
        omop_table_options(cluster_on=omop_primary_key_index_name("care_site")),
    )

    care_site_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    care_site_name: so.Mapped[Optional[str]] = so.mapped_column(sa.String(255), nullable=True)
    place_of_service_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    location_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey(role_fk(Role.PRIMARY, "location.location_id")), nullable=True)
    care_site_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)
    place_of_service_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)

    def __repr__(self) -> str:
        return f"<CareSite {self.care_site_id}>"
