from __future__ import annotations

import sqlalchemy as sa
import sqlalchemy.orm as so
from sqlalchemy.ext.hybrid import hybrid_property
from typing import Optional
from datetime import date, datetime
from oa_configurator import Role
from orm_loader.helpers import Base
from omop_alchemy.cdm.base import (
    CDMTableBase,
    cdm_table,
    optional_concept_fk,
    role_fk,
    ValueMixin,
    merge_table_args,
    omop_index,
)

@cdm_table
class Measurement(Base, CDMTableBase, ValueMixin):
    __tablename__ = "measurement"
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "person_id", cluster=True),
        omop_index(__tablename__, "measurement_concept_id"),
        omop_index(__tablename__, "visit_occurrence_id"),
        # this one is not present in the CDM DDL but is needed for efficient querying of modifiers
        omop_index(__tablename__, "meas_event_field_concept_id"),
    )

    measurement_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    person_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey("person.person_id"), nullable=False)
    measurement_concept_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")), nullable=False)
    measurement_date: so.Mapped[date] = so.mapped_column(nullable=False)
    measurement_datetime: so.Mapped[Optional[datetime]]
    measurement_time: so.Mapped[Optional[str]]
    measurement_type_concept_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")), nullable=False)
    operator_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    unit_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()

    range_low: so.Mapped[Optional[float]]
    range_high: so.Mapped[Optional[float]]

    provider_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey("provider.provider_id"))
    visit_occurrence_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey("visit_occurrence.visit_occurrence_id"))
    visit_detail_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey("visit_detail.visit_detail_id"))

    measurement_source_value: so.Mapped[Optional[str]]
    measurement_source_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    unit_source_value: so.Mapped[Optional[str]]
    unit_source_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()

    value_source_value: so.Mapped[Optional[str]]
    measurement_event_id: so.Mapped[Optional[int]]
    meas_event_field_concept_id: so.Mapped[Optional[int]] = optional_concept_fk(doc="Identifies which OMOP table measurement_event_id refers to")

    @hybrid_property
    def modifier_of_event_id(self) -> Optional[int]:
        return self.measurement_event_id

    @hybrid_property
    def modifier_of_field_concept_id(self) -> Optional[int]:
        return self.meas_event_field_concept_id
