import sqlalchemy as sa
import sqlalchemy.orm as so
from typing import Optional, TYPE_CHECKING
from datetime import date
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.ext.hybrid import hybrid_method
from sqlalchemy.orm.exc import DetachedInstanceError

from orm_loader.helpers import Base
from omop_alchemy.cdm.base import (
    cdm_table,
    CDMTableBase,
    ReferenceContext,
    required_concept_fk,
    optional_concept_fk,    
    DomainValidationMixin,
    ExpectedDomain,
    merge_table_args,
    omop_index,

)
if TYPE_CHECKING:
    from ..clinical.person import Person
    from ..health_system.care_site import Care_Site

from ..clinical.procedure_occurrence import Procedure_Occurrence
from ..health_system.provider import Provider


@cdm_table
class Visit_Occurrence(CDMTableBase, Base):
    __tablename__ = "visit_occurrence"
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "person_id", cluster=True),
        omop_index(__tablename__, "visit_concept_id"),
    )

    visit_occurrence_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    person_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey("person.person_id"), nullable=False)
    
    visit_concept_id: so.Mapped[int] = required_concept_fk()
    visit_start_date: so.Mapped[date] = so.mapped_column(sa.Date, nullable=False)
    visit_start_datetime: so.Mapped[Optional[date]] = so.mapped_column(sa.DateTime, nullable=True)
    visit_end_date: so.Mapped[date] = so.mapped_column(sa.Date, nullable=False)
    visit_end_datetime: so.Mapped[Optional[date]] = so.mapped_column(sa.DateTime, nullable=True)
    visit_type_concept_id: so.Mapped[int] = required_concept_fk()

    provider_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey("provider.provider_id"), nullable=True)
    care_site_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey("care_site.care_site_id"), nullable=True)

    visit_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)
    visit_source_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    admitted_from_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    admitted_from_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)
    discharged_to_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    discharged_to_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)

    preceding_visit_occurrence_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey("visit_occurrence.visit_occurrence_id"), nullable=True)

    def __repr__(self) -> str:
        return f"<VisitOccurrence {self.visit_occurrence_id}>"


class VisitContext(ReferenceContext):
    person: so.Mapped["Person"] = ReferenceContext._reference_relationship(target="Person", local_fk="person_id",)  # type: ignore[assignment]
    provider: so.Mapped["Provider"] = ReferenceContext._reference_relationship(target="Provider",local_fk="provider_id",)  # type: ignore[assignment]
    care_site: so.Mapped["Care_Site"] = ReferenceContext._reference_relationship(target="Care_Site",local_fk="care_site_id",)  # type: ignore[assignment]

    @declared_attr
    def procedure_providers(cls) -> so.Mapped[list["Provider"]]:
        return so.relationship(
            "Provider",
            secondary="procedure_occurrence",
            primaryjoin="Visit_Occurrence.visit_occurrence_id == Procedure_Occurrence.visit_occurrence_id",
            secondaryjoin="Provider.provider_id == Procedure_Occurrence.provider_id",
            viewonly=True,
            lazy="selectin",
        )

    @declared_attr
    def observation_providers(cls) -> so.Mapped[list["Provider"]]:
        return so.relationship(
            "Provider",
            secondary="observation",
            primaryjoin="Visit_Occurrence.visit_occurrence_id == Observation.visit_occurrence_id",
            secondaryjoin="Provider.provider_id == Observation.provider_id",
            viewonly=True,
            lazy="selectin",
        )

class VisitView(Visit_Occurrence, VisitContext, DomainValidationMixin):
    __tablename__ = "visit_occurrence"
    __mapper_args__ = {"concrete": False}
    __expected_domains__ = {
        "visit_concept_id": ExpectedDomain("Visit"),
        "visit_type_concept_id": ExpectedDomain("Type Concept"),
        "admitted_from_concept_id": ExpectedDomain("Visit"),
        "discharged_to_concept_id": ExpectedDomain("Visit"),
    }

    @property
    def all_providers(self) -> set["Provider"]:
        providers: set[Provider] = set()
        providers.update(getattr(self, "procedure_providers", []) or [])
        providers.update(getattr(self, "observation_providers", []) or [])
        providers.add(self.provider)
        return providers

    @hybrid_method
    def has_provider_specialty(self, specialty_concept_id: int) -> bool: 
        for p in self.all_providers:
            try:
                if p.specialty_source_concept_id == specialty_concept_id:
                    return True
            except DetachedInstanceError:
                pass
        return False

    @has_provider_specialty.expression
    @classmethod
    def _has_provider_specialty(cls, specialty_concept_id: int) -> sa.sql.expression.ColumnElement[bool]:  
        return sa.exists().where(
            sa.and_(
                Provider.provider_id.in_(
                    sa.select(Procedure_Occurrence.provider_id).where(
                        Procedure_Occurrence.visit_occurrence_id == cls.visit_occurrence_id
                    )
                ),
                Provider.specialty_source_concept_id == specialty_concept_id,
            )
        )
