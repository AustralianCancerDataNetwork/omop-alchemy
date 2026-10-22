import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role
from orm_loader.helpers import Base

from omop_alchemy.cdm.base import (
    CDMTableBase,
    cdm_table,
    merge_table_args,
    omop_index,
    omop_primary_key_index_name,
    omop_table_options,
    optional_concept_fk,
    role_fk,
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
    care_site_name: so.Mapped[str | None] = so.mapped_column(sa.String(255), nullable=True)
    place_of_service_concept_id: so.Mapped[int | None] = optional_concept_fk()
    location_id: so.Mapped[int | None] = so.mapped_column(
        sa.ForeignKey(role_fk(Role.PRIMARY, "location.location_id")), nullable=True
    )
    care_site_source_value: so.Mapped[str | None] = so.mapped_column(sa.String(50), nullable=True)
    place_of_service_source_value: so.Mapped[str | None] = so.mapped_column(
        sa.String(50), nullable=True
    )

    def __repr__(self) -> str:
        return f"<CareSite {self.care_site_id}>"
