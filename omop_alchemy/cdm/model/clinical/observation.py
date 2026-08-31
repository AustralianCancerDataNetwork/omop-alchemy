from __future__ import annotations

import sqlalchemy as sa
import sqlalchemy.orm as so
from sqlalchemy.ext.hybrid import hybrid_property
from typing import Optional, TYPE_CHECKING
from datetime import date, datetime
from orm_loader.helpers import Base
from omop_alchemy.cdm.base import (
    CDMTableBase,
    DomainValidationMixin,
    ExpectedDomain,
    ModifierFieldConcepts,
    ModifierTargetMixin,
    ReferenceContext,
    cdm_table,
    ValueMixin,
    merge_table_args,
    omop_index,
)

if TYPE_CHECKING:
    from ..health_system import Provider, Visit_Detail, Visit_Occurrence
    from ..vocabulary import Concept
    from .person import Person


@cdm_table
class Observation(Base, CDMTableBase, ValueMixin):
    __tablename__ = "observation"
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "person_id", cluster=True),
        omop_index(__tablename__, "observation_concept_id"),
        omop_index(__tablename__, "visit_occurrence_id"),
        omop_index(__tablename__, "obs_event_field_concept_id"),
    )

    observation_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    person_id: so.Mapped[int] = so.mapped_column(
        sa.ForeignKey("person.person_id"), nullable=False
    )
    observation_concept_id: so.Mapped[int] = so.mapped_column(
        sa.ForeignKey("concept.concept_id"), nullable=False
    )
    observation_date: so.Mapped[date] = so.mapped_column(nullable=False)
    observation_datetime: so.Mapped[Optional[datetime]]
    observation_type_concept_id: so.Mapped[int] = so.mapped_column(
        sa.ForeignKey("concept.concept_id"), nullable=False
    )
    # value_as_number: so.Mapped[Optional[float]]
    value_as_string: so.Mapped[Optional[str]]
    # value_as_concept_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey("concept.concept_id"))
    qualifier_concept_id: so.Mapped[Optional[int]] = so.mapped_column(
        sa.ForeignKey("concept.concept_id")
    )
    unit_concept_id: so.Mapped[Optional[int]] = so.mapped_column(
        sa.ForeignKey("concept.concept_id")
    )
    provider_id: so.Mapped[Optional[int]] = so.mapped_column(
        sa.ForeignKey("provider.provider_id")
    )
    visit_occurrence_id: so.Mapped[Optional[int]] = so.mapped_column(
        sa.ForeignKey("visit_occurrence.visit_occurrence_id")
    )
    visit_detail_id: so.Mapped[Optional[int]] = so.mapped_column(
        sa.ForeignKey("visit_detail.visit_detail_id")
    )
    observation_source_value: so.Mapped[Optional[str]]
    observation_source_concept_id: so.Mapped[Optional[int]] = so.mapped_column(
        sa.ForeignKey("concept.concept_id")
    )
    unit_source_value: so.Mapped[Optional[str]]
    qualifier_source_value: so.Mapped[Optional[str]]
    value_source_value: so.Mapped[Optional[str]]
    observation_event_id: so.Mapped[Optional[int]]
    obs_event_field_concept_id: so.Mapped[Optional[int]] = so.mapped_column(
        sa.ForeignKey("concept.concept_id")
    )

    @hybrid_property
    def modifier_of_event_id(self) -> Optional[int]:
        return self.observation_event_id

    @hybrid_property
    def modifier_of_field_concept_id(self) -> Optional[int]:
        return self.obs_event_field_concept_id


class ObservationContext(ReferenceContext):
    """Read-only analytical relationships for an Observation row."""

    person: so.Mapped["Person"] = ReferenceContext._reference_relationship(
        target="Person", local_fk="person_id", remote_pk="person_id"
    )  # type: ignore[assignment]
    observation_concept: so.Mapped["Concept"] = (
        ReferenceContext._reference_relationship(
            target="Concept", local_fk="observation_concept_id", remote_pk="concept_id"
        )
    )  # type: ignore[assignment]
    observation_type_concept: so.Mapped["Concept"] = (
        ReferenceContext._reference_relationship(
            target="Concept",
            local_fk="observation_type_concept_id",
            remote_pk="concept_id",
        )
    )  # type: ignore[assignment]
    provider: so.Mapped[Optional["Provider"]] = (
        ReferenceContext._reference_relationship(
            target="Provider", local_fk="provider_id", remote_pk="provider_id"
        )
    )  # type: ignore[assignment]
    visit_occurrence: so.Mapped[Optional["Visit_Occurrence"]] = (
        ReferenceContext._reference_relationship(
            target="Visit_Occurrence",
            local_fk="visit_occurrence_id",
            remote_pk="visit_occurrence_id",
        )
    )  # type: ignore[assignment]
    visit_detail: so.Mapped[Optional["Visit_Detail"]] = (
        ReferenceContext._reference_relationship(
            target="Visit_Detail",
            local_fk="visit_detail_id",
            remote_pk="visit_detail_id",
        )
    )  # type: ignore[assignment]


class ObservationView(
    Observation,
    ObservationContext,
    DomainValidationMixin,
    ModifierTargetMixin,
):
    """Analytical Observation mapping with event metadata and reference context."""

    __tablename__ = "observation"
    __mapper_args__ = {"concrete": False}
    __event_id_col__ = "observation_id"
    __concept_id_col__ = "observation_concept_id"
    __start_date_col__ = "observation_date"
    __end_date_col__ = "observation_date"
    __type_concept_id_col__ = "observation_type_concept_id"
    __expected_domains__ = {
        "observation_type_concept_id": ExpectedDomain("Type Concept"),
    }

    @classmethod
    def modifier_field_concept_id(cls) -> int:
        return ModifierFieldConcepts.OBSERVATION
