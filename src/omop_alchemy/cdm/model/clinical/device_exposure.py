from datetime import date, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, Optional

import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role
from orm_loader.helpers import Base

from omop_alchemy.cdm.base import (
    CDMTableBase,
    ClinicalEventMixin,
    DomainValidationMixin,
    ExpectedDomain,
    FactTable,
    HealthSystemContext,
    ModifierFieldConcepts,
    PersonScoped,
    ReferenceContext,
    cdm_table,
    merge_table_args,
    omop_index,
    optional_concept_fk,
    optional_int,
    required_concept_fk,
)

if TYPE_CHECKING:
    from ..health_system import Provider, Visit_Detail, Visit_Occurrence
    from ..vocabulary import Concept
    from .person import Person


@cdm_table
class Device_Exposure(
    PersonScoped,
    CDMTableBase,
    FactTable,
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
    device_exposure_start_datetime: so.Mapped[datetime | None] = so.mapped_column(
        sa.DateTime, nullable=True
    )
    device_exposure_end_datetime: so.Mapped[datetime | None] = so.mapped_column(
        sa.DateTime, nullable=True
    )

    device_concept_id: so.Mapped[int] = required_concept_fk()
    device_type_concept_id: so.Mapped[int] = required_concept_fk()
    device_source_concept_id: so.Mapped[int | None] = optional_concept_fk()
    unit_concept_id: so.Mapped[int | None] = optional_concept_fk()
    unit_source_concept_id: so.Mapped[int | None] = optional_concept_fk()

    unique_device_id: so.Mapped[str | None] = so.mapped_column(sa.String(255), nullable=True)
    production_id: so.Mapped[str | None] = so.mapped_column(sa.String(255), nullable=True)
    quantity: so.Mapped[int | None] = optional_int()
    device_source_value: so.Mapped[str | None] = so.mapped_column(sa.String(50))
    unit_source_value: so.Mapped[str | None] = so.mapped_column(sa.String(50))


class Device_ExposureContext(ReferenceContext):
    """Read-only analytical relationships for a Device Exposure row."""

    person: so.Mapped["Person"] = ReferenceContext._reference_relationship(
        target="Person", local_fk="person_id"
    )
    device_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(
        target="Concept", local_fk="device_concept_id"
    )
    device_type_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(
        target="Concept",
        local_fk="device_type_concept_id",
    )
    device_source_concept: so.Mapped[Optional["Concept"]] = (
        ReferenceContext._reference_relationship(
            target="Concept",
            local_fk="device_source_concept_id",
        )
    )
    unit_concept: so.Mapped[Optional["Concept"]] = ReferenceContext._reference_relationship(
        target="Concept", local_fk="unit_concept_id"
    )
    unit_source_concept: so.Mapped[Optional["Concept"]] = ReferenceContext._reference_relationship(
        target="Concept",
        local_fk="unit_source_concept_id",
    )
    provider: so.Mapped[Optional["Provider"]] = ReferenceContext._reference_relationship(
        target="Provider", local_fk="provider_id"
    )
    visit_occurrence: so.Mapped[Optional["Visit_Occurrence"]] = (
        ReferenceContext._reference_relationship(
            target="Visit_Occurrence",
            local_fk="visit_occurrence_id",
        )
    )
    visit_detail: so.Mapped[Optional["Visit_Detail"]] = ReferenceContext._reference_relationship(
        target="Visit_Detail",
        local_fk="visit_detail_id",
    )


class Device_ExposureView(
    Device_Exposure,
    Device_ExposureContext,
    DomainValidationMixin,
    ClinicalEventMixin,
):
    """Analytical Device Exposure mapping with event metadata and references."""

    __tablename__ = "device_exposure"
    # Must match Device_Exposure's schema, or SQLAlchemy silently builds a second, unlinked Table object.
    __table_args__ = ({"schema": Role.PRIMARY.value},)
    __mapper_args__ = MappingProxyType({"concrete": False})
    __event_id_col__ = "device_exposure_id"
    __concept_id_col__ = "device_concept_id"
    __start_date_col__ = "device_exposure_start_date"
    __end_date_col__ = "device_exposure_end_date"
    __type_concept_id_col__ = "device_type_concept_id"
    __expected_domains__: ClassVar[dict[str, ExpectedDomain]] = {
        "device_concept_id": ExpectedDomain("Device"),
        "device_type_concept_id": ExpectedDomain("Type Concept"),
    }

    @classmethod
    def modifier_field_concept_id(cls) -> int:
        return ModifierFieldConcepts.DEVICE_EXPOSURE
