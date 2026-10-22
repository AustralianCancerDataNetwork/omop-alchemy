from __future__ import annotations

from datetime import date, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar

import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role
from orm_loader.helpers import Base

from omop_alchemy.cdm.base import (
    CDMTableBase,
    ClinicalEventMixin,
    DomainValidationMixin,
    ExpectedDomain,
    ModifierFieldConcepts,
    ModifierSourceMixin,
    ReferenceContext,
    ValueMixin,
    cdm_table,
    merge_table_args,
    omop_index,
    optional_concept_fk,
    role_fk,
)

if TYPE_CHECKING:
    from ..health_system import Provider, Visit_Detail, Visit_Occurrence
    from ..vocabulary import Concept
    from .person import Person


@cdm_table
class Observation(Base, CDMTableBase, ValueMixin, ModifierSourceMixin):
    __tablename__ = "observation"
    __table_args__ = merge_table_args(
        {"schema": Role.PRIMARY.value},
        omop_index(__tablename__, "person_id", cluster=True),
        omop_index(__tablename__, "observation_concept_id"),
        omop_index(__tablename__, "visit_occurrence_id"),
        omop_index(__tablename__, "obs_event_field_concept_id"),
    )

    observation_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    person_id: so.Mapped[int] = so.mapped_column(
        sa.ForeignKey(role_fk(Role.PRIMARY, "person.person_id")), nullable=False
    )
    observation_concept_id: so.Mapped[int] = so.mapped_column(
        sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")), nullable=False
    )
    observation_date: so.Mapped[date] = so.mapped_column(nullable=False)
    observation_datetime: so.Mapped[datetime | None]
    observation_type_concept_id: so.Mapped[int] = so.mapped_column(
        sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")), nullable=False
    )
    # value_as_number: so.Mapped[Optional[float]]
    value_as_string: so.Mapped[str | None]
    # value_as_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    qualifier_concept_id: so.Mapped[int | None] = optional_concept_fk()
    unit_concept_id: so.Mapped[int | None] = optional_concept_fk()
    provider_id: so.Mapped[int | None] = so.mapped_column(
        sa.ForeignKey(role_fk(Role.PRIMARY, "provider.provider_id"))
    )
    visit_occurrence_id: so.Mapped[int | None] = so.mapped_column(
        sa.ForeignKey(role_fk(Role.PRIMARY, "visit_occurrence.visit_occurrence_id"))
    )
    visit_detail_id: so.Mapped[int | None] = so.mapped_column(
        sa.ForeignKey(role_fk(Role.PRIMARY, "visit_detail.visit_detail_id"))
    )
    observation_source_value: so.Mapped[str | None]
    observation_source_concept_id: so.Mapped[int | None] = optional_concept_fk()
    unit_source_value: so.Mapped[str | None]
    qualifier_source_value: so.Mapped[str | None]
    value_source_value: so.Mapped[str | None]
    observation_event_id: so.Mapped[int | None]
    obs_event_field_concept_id: so.Mapped[int | None] = optional_concept_fk()

    __modifier_event_id_col__ = "observation_event_id"
    __modifier_field_concept_id_col__ = "obs_event_field_concept_id"


class ObservationContext(ReferenceContext):
    """Read-only analytical relationships for an Observation row."""

    person: so.Mapped[Person] = ReferenceContext._reference_relationship(
        target="Person", local_fk="person_id"
    )  # type: ignore[assignment]
    observation_concept: so.Mapped[Concept] = ReferenceContext._reference_relationship(
        target="Concept", local_fk="observation_concept_id"
    )  # type: ignore[assignment]
    observation_type_concept: so.Mapped[Concept] = ReferenceContext._reference_relationship(
        target="Concept",
        local_fk="observation_type_concept_id",
    )  # type: ignore[assignment]
    unit_concept: so.Mapped[Concept | None] = ReferenceContext._reference_relationship(
        target="Concept", local_fk="unit_concept_id"
    )  # type: ignore[assignment]
    provider: so.Mapped[Provider | None] = ReferenceContext._reference_relationship(
        target="Provider", local_fk="provider_id"
    )  # type: ignore[assignment]
    visit_occurrence: so.Mapped[Visit_Occurrence | None] = ReferenceContext._reference_relationship(
        target="Visit_Occurrence",
        local_fk="visit_occurrence_id",
    )  # type: ignore[assignment]
    visit_detail: so.Mapped[Visit_Detail | None] = ReferenceContext._reference_relationship(
        target="Visit_Detail",
        local_fk="visit_detail_id",
    )  # type: ignore[assignment]


class ObservationView(
    Observation,
    ObservationContext,
    DomainValidationMixin,
    ClinicalEventMixin,
):
    """Analytical Observation mapping with event metadata and reference context."""

    __tablename__ = "observation"
    # Must match Observation's schema, or SQLAlchemy silently builds a second, unlinked Table object.
    __table_args__ = ({"schema": Role.PRIMARY.value},)
    __mapper_args__ = MappingProxyType({"concrete": False})
    __event_id_col__ = "observation_id"
    __concept_id_col__ = "observation_concept_id"
    __start_date_col__ = "observation_date"
    # One date column: the target API aliases it; interval metadata treats
    # equal start/end declarations as a point with no independent endpoint.
    __end_date_col__ = "observation_date"
    __type_concept_id_col__ = "observation_type_concept_id"
    __expected_domains__: ClassVar[dict[str, ExpectedDomain]] = {
        "observation_type_concept_id": ExpectedDomain("Type Concept"),
    }

    @classmethod
    def modifier_field_concept_id(cls) -> int:
        return ModifierFieldConcepts.OBSERVATION
