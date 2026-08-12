import sqlalchemy as sa
import sqlalchemy.orm as so
from sqlalchemy.ext.declarative import declared_attr
from typing import ClassVar, Optional, TYPE_CHECKING, List
from datetime import date
from orm_loader.helpers import Base 
from omop_alchemy.cdm.base import (
    cdm_table,
    CDMTableBase, 
    required_concept_fk,
    optional_concept_fk,
    PersonScoped,
    optional_int,
    ReferenceContext,
    DomainValidationMixin,
    ExpectedDomain,
    merge_table_args,
    omop_index,
)

if TYPE_CHECKING:
    from ..vocabulary import Concept
    from ..clinical import Person
    from .episode_event import Episode_EventView
    from ...base.typing import HasEpisodeId

@cdm_table
class Episode(CDMTableBase, Base, PersonScoped):
    __tablename__ = "episode"
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "person_id", cluster=True),
        omop_index(__tablename__, "episode_concept_id"),
        # following indices are not specified in the cdm but are likely to be useful for query performance
        omop_index(__tablename__, "episode_object_concept_id"),
        omop_index(__tablename__, "episode_type_concept_id"),
        omop_index(__tablename__, "episode_parent_id"),
    )

    episode_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    episode_parent_id: so.Mapped[Optional[int]] = so.mapped_column(sa.ForeignKey("episode.episode_id"), nullable=True)

    episode_start_date: so.Mapped[date] = so.mapped_column(sa.Date, nullable=False)
    episode_start_datetime: so.Mapped[Optional[date]] = so.mapped_column(sa.DateTime, nullable=True)
    episode_end_date: so.Mapped[Optional[date]] = so.mapped_column(sa.Date, nullable=True)
    episode_end_datetime: so.Mapped[Optional[date]] = so.mapped_column(sa.DateTime, nullable=True)

    episode_number: so.Mapped[Optional[int]] = optional_int()
    episode_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)

    episode_concept_id: so.Mapped[int] = required_concept_fk()
    episode_object_concept_id: so.Mapped[int] = required_concept_fk()
    episode_type_concept_id: so.Mapped[int] = required_concept_fk()
    episode_source_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()

    def __repr__(self) -> str:
        return f"<Episode {self.episode_id}>"
    
class EpisodeContext(ReferenceContext):
    __table__: ClassVar[sa.Table]

    person: so.Mapped["Person"] = ReferenceContext._reference_relationship(target="Person",local_fk="person_id",remote_pk="person_id")  # type: ignore[assignment]
    episode_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(target="Concept",local_fk="episode_concept_id",remote_pk="concept_id")  # type: ignore[assignment]
    episode_object_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(target="Concept",local_fk="episode_object_concept_id",remote_pk="concept_id")  # type: ignore[assignment]
    episode_type_concept: so.Mapped["Concept"] = ReferenceContext._reference_relationship(target="Concept",local_fk="episode_type_concept_id",remote_pk="concept_id")  # type: ignore[assignment]
    #parent_episode: so.Mapped[Optional["Episode"]] = ReferenceContext._reference_relationship(target="Episode",local_fk="episode_parent_id",remote_pk="episode_id")  # type: ignore[assignment]
    
    @declared_attr
    def episode_events(cls: type['HasEpisodeId']) -> so.Mapped[List["Episode_EventView"]]:
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
    def children(cls) -> so.Mapped[List["Episode"]]:
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

class EpisodeView(Episode, EpisodeContext, DomainValidationMixin):
    """
    Navigable Episode view.

    Use for:
    - disease phase modelling
    - lines of therapy
    - episode hierarchies
    """

    __tablename__ = "episode"
    __mapper_args__ = {"concrete": False}

    __expected_domains__ = {
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
        return (
            f"<Episode {self.episode_id}: "
            f"{self.episode_concept_id} "
            f"({self.episode_start_date})>"
        )
