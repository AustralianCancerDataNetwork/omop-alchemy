from datetime import date
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, Optional, Self

import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role
from orm_loader.helpers import Base
from sqlalchemy.ext.declarative import declared_attr

from omop_alchemy.cdm.base import (
    CDMTableBase,
    DomainValidationMixin,
    ExpectedDomain,
    ModifierFieldConcepts,
    ModifierTargetMixin,
    PersonScoped,
    ReferenceContext,
    cdm_table,
    merge_table_args,
    omop_index,
    optional_concept_fk,
    optional_int,
    required_concept_fk,
    role_fk,
)

if TYPE_CHECKING:
    from ...base.typing import HasEpisodeId
    from ..clinical import Person
    from ..vocabulary import Concept
    from .episode_event import Episode_EventView


@cdm_table
class Episode(CDMTableBase, Base, PersonScoped):
    __tablename__ = "episode"
    __table_args__ = merge_table_args(
        {"schema": Role.PRIMARY.value},
        omop_index(__tablename__, "person_id", cluster=True),
        omop_index(__tablename__, "episode_concept_id"),
        # following indices are not specified in the cdm but are likely to be useful for query performance
        omop_index(__tablename__, "episode_object_concept_id"),
        omop_index(__tablename__, "episode_type_concept_id"),
        omop_index(__tablename__, "episode_parent_id"),
    )

    episode_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    episode_parent_id: so.Mapped[int | None] = so.mapped_column(
        sa.ForeignKey(role_fk(Role.PRIMARY, "episode.episode_id")), nullable=True
    )

    episode_start_date: so.Mapped[date] = so.mapped_column(sa.Date, nullable=False)
    episode_start_datetime: so.Mapped[date | None] = so.mapped_column(sa.DateTime, nullable=True)
    episode_end_date: so.Mapped[date | None] = so.mapped_column(sa.Date, nullable=True)
    episode_end_datetime: so.Mapped[date | None] = so.mapped_column(sa.DateTime, nullable=True)

    episode_number: so.Mapped[int | None] = optional_int()
    episode_source_value: so.Mapped[str | None] = so.mapped_column(sa.String(50), nullable=True)

    episode_concept_id: so.Mapped[int] = required_concept_fk()
    episode_object_concept_id: so.Mapped[int] = required_concept_fk()
    episode_type_concept_id: so.Mapped[int] = required_concept_fk()
    episode_source_concept_id: so.Mapped[int | None] = optional_concept_fk()

    def __repr__(self) -> str:
        return f"<Episode {self.episode_id}>"


class EpisodeContext(ReferenceContext):
    __table__: ClassVar[sa.Table]

    person: so.Mapped["Person"] = ReferenceContext._reference_relationship(
        target="Person", local_fk="person_id"
    )  # type: ignore[assignment]
    episode_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(
        target="Concept", local_fk="episode_concept_id"
    )  # type: ignore[assignment]
    episode_object_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(
        target="Concept", local_fk="episode_object_concept_id"
    )  # type: ignore[assignment]
    episode_type_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(
        target="Concept", local_fk="episode_type_concept_id"
    )  # type: ignore[assignment]
    # parent_episode: so.Mapped[Optional["Episode"]] = ReferenceContext._reference_relationship(target="Episode",local_fk="episode_parent_id")  # type: ignore[assignment]

    @declared_attr
    def episode_events(cls: type["HasEpisodeId"]) -> so.Mapped[list["Episode_EventView"]]:
        return so.relationship(
            "Episode_EventView",
            primaryjoin="Episode.episode_id == Episode_EventView.episode_id",
            viewonly=True,
            lazy="selectin",
        )

    @declared_attr
    def parent_episode(cls) -> so.Mapped[Optional["Episode"]]:
        return so.relationship(
            "Episode",
            primaryjoin=lambda: Episode.episode_parent_id == so.remote(Episode.episode_id),
            foreign_keys=lambda: [Episode.episode_parent_id],
            remote_side=lambda: [Episode.episode_id],
            viewonly=True,
            lazy="selectin",
            uselist=False,
        )

    @declared_attr
    @classmethod
    def children(cls) -> so.Mapped[list[Self]]:
        episode_id = cls.__table__.c.episode_id
        episode_parent_id = cls.__table__.c.episode_parent_id
        return so.relationship(
            cls.__name__,
            primaryjoin=lambda: so.remote(episode_parent_id) == episode_id,
            foreign_keys=lambda: [episode_parent_id],
            remote_side=lambda: [episode_parent_id],
            viewonly=True,
            lazy="selectin",
            uselist=True,
        )


class EpisodeView(Episode, EpisodeContext, DomainValidationMixin, ModifierTargetMixin):
    """
    Navigable Episode view.

    Use for:
    - disease phase modelling
    - lines of therapy
    - episode hierarchies
    """

    __tablename__ = "episode"
    # Must match Episode's schema, or SQLAlchemy silently builds a second, unlinked Table object.
    __table_args__ = ({"schema": Role.PRIMARY.value},)
    __mapper_args__ = MappingProxyType({"concrete": False})
    __event_id_col__ = "episode_id"
    __concept_id_col__ = "episode_concept_id"
    __start_date_col__ = "episode_start_date"
    __end_date_col__ = "episode_end_date"
    __type_concept_id_col__ = "episode_type_concept_id"

    @classmethod
    def modifier_field_concept_id(cls) -> int:
        return ModifierFieldConcepts.EPISODE

    __expected_domains__: ClassVar[dict[str, ExpectedDomain]] = {
        "episode_concept_id": ExpectedDomain("Episode"),
        "episode_object_concept_id": ExpectedDomain("Condition", "Procedure", "Regimen"),
        "episode_type_concept_id": ExpectedDomain("Type Concept"),
    }

    @property
    def events(self) -> list[object]:
        """
        All linked OMOP event rows (Condition_Occurrence, Drug_Exposure, etc).
        """
        if not self.episode_events:
            return []

        resolved: list[object] = []
        for ee in self.episode_events:
            target = ee.resolved_event
            if target is not None:
                resolved.append(target)

        return resolved

    def __repr__(self) -> str:
        return f"<Episode {self.episode_id}: {self.episode_concept_id} ({self.episode_start_date})>"
