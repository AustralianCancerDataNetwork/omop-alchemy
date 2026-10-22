from datetime import date
from typing import TYPE_CHECKING

import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role
from orm_loader.helpers import Base

from omop_alchemy.cdm.base import (
    CDMTableBase,
    DomainValidationMixin,
    ReferenceContext,
    cdm_table,
    merge_table_args,
    omop_primary_key_index_name,
    omop_table_options,
    optional_concept_fk,
    role_fk,
)

if TYPE_CHECKING:
    from ..clinical import Person, PersonView
    from ..vocabulary import Concept


@cdm_table
class Death(CDMTableBase, Base):
    __tablename__ = "death"
    __table_args__ = merge_table_args(
        {"schema": Role.PRIMARY.value},
        omop_table_options(cluster_on=omop_primary_key_index_name("death")),
    )
    person_id: so.Mapped[int] = so.mapped_column(
        sa.ForeignKey(role_fk(Role.PRIMARY, "person.person_id")), primary_key=True
    )
    death_date: so.Mapped[date] = so.mapped_column(nullable=False)
    death_datetime: so.Mapped[date | None] = so.mapped_column(sa.DateTime, nullable=True)
    death_type_concept_id: so.Mapped[int | None] = optional_concept_fk()
    cause_concept_id: so.Mapped[int | None] = optional_concept_fk()
    cause_source_value: so.Mapped[str | None] = so.mapped_column(sa.String(50), nullable=True)
    cause_source_concept_id: so.Mapped[int | None] = optional_concept_fk()


class DeathContext(ReferenceContext):
    person: so.Mapped["Person"] = ReferenceContext._reference_relationship(
        target="Person", local_fk="person_id"
    )  # type: ignore[assignment]
    death_type_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(
        target="Concept", local_fk="death_type_concept_id"
    )  # type: ignore[assignment]
    cause_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(
        target="Concept", local_fk="cause_concept_id"
    )  # type: ignore[assignment]


class DeathView(Death, DeathContext, DomainValidationMixin):
    __tablename__ = "death"
    # Must match Death's schema, or SQLAlchemy silently builds a second, unlinked Table object.
    __table_args__ = {"schema": Role.PRIMARY.value}
    __mapper_args__ = {"concrete": False}
    __expected_domains__ = {}
    person_view: so.Mapped["PersonView"] = so.relationship(
        "PersonView",
        viewonly=True,
        lazy="joined",
    )
