import sqlalchemy as sa
import sqlalchemy.orm as so
from typing import Optional, TYPE_CHECKING
from datetime import date, datetime
from orm_loader.helpers import Base
from omop_alchemy.cdm.base import (
    PersonScoped, 
    HealthSystemContext, 
    FactTable, 
    ReferenceContext,
    CDMTableBase,
    cdm_table, 
    required_concept_fk,
    optional_concept_fk,
    optional_int,
    ModifierTargetMixin,
    ModifierFieldConcepts,
    merge_table_args,
    omop_index,
)

if TYPE_CHECKING:
    from ..vocabulary import Concept

@cdm_table
class Drug_Exposure(
    PersonScoped,
    CDMTableBase,
    FactTable,
    HealthSystemContext,
    Base,
):
    __tablename__ = "drug_exposure"
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "person_id", cluster=True),
        omop_index(__tablename__, "drug_concept_id"),
        omop_index(__tablename__, "visit_occurrence_id")
    )

    drug_exposure_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    
    drug_exposure_start_date: so.Mapped[date] = so.mapped_column(sa.Date, nullable=False)
    drug_exposure_end_date: so.Mapped[date] = so.mapped_column(sa.Date, nullable=False)
    drug_exposure_start_datetime: so.Mapped[Optional[datetime]] = so.mapped_column(sa.DateTime, nullable=True)
    drug_exposure_end_datetime: so.Mapped[Optional[datetime]] = so.mapped_column(sa.DateTime, nullable=True)
    verbatim_end_date: so.Mapped[Optional[date]] = so.mapped_column(sa.Date, nullable=True)
    
    drug_concept_id: so.Mapped[int] = required_concept_fk()
    drug_type_concept_id: so.Mapped[int] = required_concept_fk()
    route_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    drug_source_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    
    refills: so.Mapped[Optional[int]] = optional_int()
    quantity: so.Mapped[Optional[float]] = so.mapped_column(sa.Float, nullable=True)
    days_supply: so.Mapped[Optional[int]] = optional_int()

    stop_reason: so.Mapped[Optional[str]] = so.mapped_column(sa.String(20))
    sig: so.Mapped[Optional[str]] = so.mapped_column(sa.Text)
    lot_number: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50))
    drug_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50))
    route_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50))
    dose_unit_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50))


class Drug_ExposureContext(ReferenceContext):
    drug_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(target="Concept", local_fk="drug_concept_id", remote_pk="concept_id")  # type: ignore[assignment]
    drug_type: so.Mapped["Concept"] = ReferenceContext._reference_relationship(target="Concept", local_fk="drug_type_concept_id", remote_pk="concept_id")  # type: ignore[assignment]
    route: so.Mapped["Concept"] = ReferenceContext._reference_relationship(target="Concept", local_fk="route_concept_id", remote_pk="concept_id")  # type: ignore[assignment]
    drug_source_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(target="Concept", local_fk="drug_source_concept_id", remote_pk="concept_id")  # type: ignore[assignment]


class Drug_ExposureView(
    Drug_Exposure,
    Drug_ExposureContext,
    ModifierTargetMixin
):

    __tablename__ = "drug_exposure"
    __mapper_args__ = {"concrete": False}

    __event_id_col__ = "drug_exposure_id"
    __concept_id_col__ = "drug_concept_id"
    __start_date_col__ = "drug_exposure_start_date"
    __end_date_col__ = "drug_exposure_end_date"
    __type_concept_id_col__ = "drug_type_concept_id"
    
    __expected_domains__ = {
    
    }

    @classmethod
    def modifier_field_concept_id(cls) -> int:
        return ModifierFieldConcepts.DRUG_EXPOSURE
    
