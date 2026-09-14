import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role
from typing import Optional
from datetime import date, datetime

from orm_loader.helpers import Base
from omop_alchemy.cdm.base import (
    PersonScoped, 
    HealthSystemContext, 
    FactTable, 
    CDMTableBase,
    cdm_table, 
    required_concept_fk,
    optional_concept_fk,
    optional_int,
    ModifierTargetMixin,
    merge_table_args,
    omop_index,
)

@cdm_table
class Device_Exposure(
    PersonScoped,
    CDMTableBase,
    FactTable,
    ModifierTargetMixin,
    HealthSystemContext,
    Base,
):
    __tablename__ = "device_exposure"
    __table_args__ = merge_table_args(
        {"schema": Role.PRIMARY.value},
        omop_index(__tablename__, "person_id", cluster=True),
        omop_index(__tablename__, "device_concept_id"),
        omop_index(__tablename__, "visit_occurrence_id"),
    )

    device_exposure_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    
    device_exposure_start_date: so.Mapped[date] = so.mapped_column(sa.Date, nullable=False)
    device_exposure_end_date: so.Mapped[date] = so.mapped_column(sa.Date, nullable=False)
    device_exposure_start_datetime: so.Mapped[Optional[datetime]] = so.mapped_column(sa.DateTime, nullable=True)
    device_exposure_end_datetime: so.Mapped[Optional[datetime]] = so.mapped_column(sa.DateTime, nullable=True)
    
    device_concept_id: so.Mapped[int] = required_concept_fk()
    device_type_concept_id: so.Mapped[int] = required_concept_fk()
    device_source_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    unit_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    unit_source_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()

    unique_device_id: so.Mapped[Optional[str]] = so.mapped_column(sa.String(255),nullable=True)
    production_id: so.Mapped[Optional[str]] = so.mapped_column(sa.String(255),nullable=True)
    quantity: so.Mapped[Optional[int]] = optional_int()
    device_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50))
    unit_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50))
