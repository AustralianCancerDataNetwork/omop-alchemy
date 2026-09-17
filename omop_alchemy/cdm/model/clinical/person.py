import sqlalchemy as sa
import sqlalchemy.orm as so
from sqlalchemy.ext.declarative import declared_attr
from typing import Optional
from datetime import date
from sqlalchemy.ext.hybrid import hybrid_method, hybrid_property
from sqlalchemy.orm.exc import DetachedInstanceError
from sqlalchemy.sql import ColumnElement
from orm_loader.helpers import Base

from omop_alchemy.cdm.base import (
    cdm_table,
    CDMTableBase, 
    required_concept_fk,
    optional_concept_fk,
    required_int,
    optional_int,
    HealthSystemContext,
    ReferenceContext,
    DomainValidationMixin,
    ExpectedDomain,
    merge_table_args,
    omop_index,
    omop_primary_key_index_name,
    omop_table_options,
)

from ..vocabulary import Concept
from ..health_system import Location, Provider, Care_Site
from .death import Death
from ..derived import Observation_Period

@cdm_table
class Person(CDMTableBase,Base,HealthSystemContext):
    __tablename__ = "person"
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "gender_concept_id"),
        omop_table_options(cluster_on=omop_primary_key_index_name("person")),
    )

    person_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    
    year_of_birth: so.Mapped[int] = required_int()
    month_of_birth: so.Mapped[Optional[int]] = optional_int()
    day_of_birth: so.Mapped[Optional[int]] = optional_int()
    birth_datetime: so.Mapped[Optional[date]] = so.mapped_column(sa.DateTime, nullable=True)
    
    gender_concept_id: so.Mapped[int] = required_concept_fk()
    race_concept_id: so.Mapped[int] = required_concept_fk()
    ethnicity_concept_id: so.Mapped[int] = required_concept_fk()
    gender_source_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    race_source_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    ethnicity_source_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()

    location_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey("location.location_id"), nullable=True)
    provider_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey("provider.provider_id"), nullable=True)
    care_site_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey("care_site.care_site_id"), nullable=True)
    
    person_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)
    gender_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)
    race_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)
    ethnicity_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)

    def __repr__(self) -> str:
        return f"<Person {self.person_id}>"

class PersonContext(ReferenceContext):
    gender: so.Mapped["Concept"] = ReferenceContext._reference_relationship(target="Concept",local_fk="gender_concept_id")  # type: ignore[assignment]
    race: so.Mapped["Concept"] = ReferenceContext._reference_relationship(target="Concept",local_fk="race_concept_id")  # type: ignore[assignment]
    ethnicity: so.Mapped["Concept"] = ReferenceContext._reference_relationship(target="Concept",local_fk="ethnicity_concept_id")  # type: ignore[assignment]
    location: so.Mapped["Location"] = ReferenceContext._reference_relationship(target="Location",local_fk="location_id")  # type: ignore[assignment]
    provider: so.Mapped["Provider"] = ReferenceContext._reference_relationship(target="Provider",local_fk="provider_id")  # type: ignore[assignment]
    care_site: so.Mapped["Care_Site"] = ReferenceContext._reference_relationship(target="Care_Site",local_fk="care_site_id")  # type: ignore[assignment]

    @declared_attr
    def death(cls) -> so.Mapped[Optional["Death"]]:
        return so.relationship(
            "Death",
            primaryjoin="Person.person_id == Death.person_id",
            foreign_keys="Death.person_id",
            uselist=False,
            viewonly=True,
            lazy="selectin",
        )
    
    @declared_attr
    def observation_periods(cls) -> so.Mapped[list["Observation_Period"]]:
        return so.relationship(
            "Observation_Period",
            primaryjoin="Person.person_id == Observation_Period.person_id",
            foreign_keys="Observation_Period.person_id",
            viewonly=True,
            lazy="selectin",
        )
    
class PersonView(Person, PersonContext, DomainValidationMixin):
    """
    Rich, navigable Person mapping.

    Use when:
    - cohort logic
    - analytics
    - inspection / debugging

    Avoid in ETL loops.
    """
    __tablename__ = "person"
    __mapper_args__ = {"concrete": False}
    __expected_domains__ = {
        "gender_concept_id": ExpectedDomain("Gender"),
        "race_concept_id": ExpectedDomain("Race"),
        "ethnicity_concept_id": ExpectedDomain("Ethnicity"),
    }

    @hybrid_method
    def age_at(self, on_date: date) -> Optional[int]:
        if not self.year_of_birth:
            return None
        return on_date.year - self.year_of_birth

    @age_at.expression
    def age_at(cls, on_date: date) -> ColumnElement[int]:
        return sa.func.extract("year", on_date) - cls.year_of_birth # type: ignore
    
    @property
    def age(self) -> Optional[int]:
        return self.age_at(date.today())
    
    @property
    def gender_code(self) -> Optional[str]:
        """
        Attempt relationship access (may lazy load) to get human-readable gender code.
        """
        try:
            gender = self.gender  # type: ignore[attr-defined]
            return gender.concept_name[:1].upper()
        except DetachedInstanceError:
            pass
        except Exception:
            pass
        return None
 
    def __repr__(self) -> str:
        """
        Compact, safe representation:
        <Person 12345: M(50)>
        <Person 67890: F(?)>
        <Person 99999: ?(?)>
        """
        pid = getattr(self, "person_id", "?")
        gender_code = self.gender_code or "?"
        age = self.age
        age_str = str(age) if age is not None else "?"
        return f"<Person {pid}: {gender_code}({age_str})>"
    
    @hybrid_property
    def is_deceased(self) -> bool: 
        return hasattr(self, "death") and self.death is not None

    @is_deceased.expression
    @classmethod
    def _is_deceased(cls):
        return sa.exists().where(Death.person_id == cls.person_id)
    
    @hybrid_property
    def has_observation_period(self) -> bool: 
        return hasattr(self, "observation_periods") and bool(self.observation_periods)

    @has_observation_period.expression
    @classmethod
    def _has_observation_period(cls):
        return sa.exists().where(
            Observation_Period.person_id == cls.person_id
        )
    
    @property
    def age_group(self) -> Optional[str]:
        if self.age is None:
            return None
        if self.age < 18:
            return "<18"
        if self.age < 40:
            return "18–39"
        if self.age < 65:
            return "40–64"
        return "65+"

    @hybrid_property
    def first_observation_date(self) -> date | None: 
        if not getattr(self, "observation_periods", None):
            return None
        starts = [op.observation_period_start_date for op in self.observation_periods]
        return min(starts) if starts else None

    @first_observation_date.expression
    @classmethod
    def _first_observation_date(cls):
        return (
            sa.select(sa.func.min(Observation_Period.observation_period_start_date))
            .where(Observation_Period.person_id == cls.person_id)
            .correlate(cls) 
            .scalar_subquery()
        )

    @hybrid_property
    def last_observation_date(self) -> date | None: 
        if not getattr(self, "observation_periods", None):
            return None
        ends = [op.observation_period_end_date for op in self.observation_periods]
        return max(ends) if ends else None

    @last_observation_date.expression
    @classmethod
    def _last_observation_date(cls):
        return (
            sa.select(sa.func.max(Observation_Period.observation_period_end_date))
            .where(Observation_Period.person_id == cls.person_id)
            .correlate(cls) 
            .scalar_subquery()
        )
    
    @hybrid_method
    def under_observation_on(self, on_date: date) -> bool: 
        if not getattr(self, "observation_periods", None):
            return False
        return any(
            op.observation_period_start_date <= on_date <= op.observation_period_end_date
            for op in self.observation_periods
        )

    @under_observation_on.expression
    @classmethod
    def _under_observation_on(
        cls, 
        on_date: date,
    ) -> ColumnElement[bool]:
        return sa.exists().where(
            sa.and_(
                Observation_Period.person_id == cls.person_id,
                Observation_Period.observation_period_start_date <= on_date,
                Observation_Period.observation_period_end_date >= on_date,
            )
        )
