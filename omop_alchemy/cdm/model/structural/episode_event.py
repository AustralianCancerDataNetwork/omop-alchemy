import sqlalchemy as sa
import sqlalchemy.orm as so
from sqlalchemy import event
from sqlalchemy.orm import Mapper
from typing import TYPE_CHECKING, Any, Type
from functools import cached_property, cache
from orm_loader.helpers import Base
from omop_alchemy.cdm.base import (
    cdm_table,
    CDMTableBase,
    MODEL_MODULE_PREFIX,
    ReferenceContext,
    DomainValidationMixin,
    ExpectedDomain,
    ModifierTargetMixin,
    merge_table_args,
    omop_index,
)

if TYPE_CHECKING:
    from ..vocabulary import Concept
    from .episode import Episode

@cache
def _modifier_target_classes_by_field_concept_id() -> dict[int, Type[Any]]:
    """
    Default field-concept -> ORM target class map, CDM classes only.

    Scans every registered mapper, so iteration order is otherwise at the
    mercy of import order. Sorting makes the result deterministic, and
    restricting to ``cdm.model`` (the same ``MODEL_MODULE_PREFIX`` that
    ``@cdm_table`` itself classifies tables by) excludes toolkit/domain
    subclasses (e.g. oncology-aware views) that also implement
    ``ModifierTargetMixin`` -- those are reached only through an explicit
    override such as ``OncologyEpisodeEvent.resolved_event_target_classes``,
    never by accident here.
    """
    classes: dict[int, Type[Any]] = {}
    mappers = sorted(
        Base.registry.mappers,
        key=lambda mapper: f"{mapper.class_.__module__}.{mapper.class_.__qualname__}",
    )
    for mapper in mappers:
        cls = mapper.class_
        if not issubclass(cls, ModifierTargetMixin):
            continue
        if not cls.__module__.startswith(MODEL_MODULE_PREFIX):
            continue
        try:
            field_concept_id = cls.modifier_field_concept_id()
        except NotImplementedError:
            continue
        if field_concept_id not in classes:
            classes[field_concept_id] = cls
    return classes

@event.listens_for(Mapper, "after_mapper_constructed")
def _invalidate_target_class_cache(
    mapper: Mapper[Any],
    class_: type[Any],
) -> None:
    _modifier_target_classes_by_field_concept_id.cache_clear()


def clear_episode_event_target_class_cache() -> None:
    """
    Clear cached episode_event field-concept target mappings.

    Invalidation is automatic: ``after_mapper_constructed`` clears the cache
    whenever a new mapper is configured, so a ``ModifierTargetMixin`` subclass
    imported late is picked up without intervention. This remains as an escape
    hatch for callers that build target classes outside the mapper lifecycle.
    """
    _modifier_target_classes_by_field_concept_id.cache_clear()


@cdm_table
class Episode_Event(CDMTableBase, Base):
    __tablename__ = "episode_event"
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "episode_id", cluster=True),
        omop_index(__tablename__, "episode_event_field_concept_id"),
    )

    episode_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey("episode.episode_id"),nullable=False,primary_key=True)
    event_id: so.Mapped[int] = so.mapped_column(nullable=False,primary_key=True)
    episode_event_field_concept_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey("concept.concept_id"),nullable=False,primary_key=True)

    def __repr__(self) -> str:
        return f"<EpisodeEvent ep={self.episode_id} event={self.event_id}>"
    
class Episode_EventContext(ReferenceContext):
    episode: so.Mapped["Episode"] = ReferenceContext._reference_relationship(target="Episode",local_fk="episode_id",remote_pk="episode_id",)  # type: ignore[assignment]
    event_field: so.Mapped["Concept"] = ReferenceContext._reference_relationship(target="Concept",local_fk="episode_event_field_concept_id",remote_pk="concept_id",)  # type: ignore[assignment]

class Episode_EventView(Episode_Event, Episode_EventContext, DomainValidationMixin):
    """
    Episode ↔ Event linkage view.

    Identifies which CDM table the EVENT_ID comes from via
    episode_event_field_concept_id.
    """

    __tablename__ = "episode_event"
    __mapper_args__ = {"concrete": False}

    __expected_domains__ = {
        "episode_event_field_concept_id": ExpectedDomain("Metadata"),
    }

    @classmethod
    def resolved_event_target_classes(cls) -> dict[int, Type[Any]]:
        """
        Map episode_event field concepts to ORM classes that can receive them.
        """
        return _modifier_target_classes_by_field_concept_id()

    @property
    def event_table(self) -> str | None:
        if self.event_field and "." in self.event_field.concept_name:
            return self.event_field.concept_name.split(".", 1)[0]
        return None

    @property
    def resolved_event_class(self) -> Type[Any] | None:
        return self.resolved_event_target_classes().get(
            self.episode_event_field_concept_id
        )

    @cached_property
    def resolved_event(self) -> Any | None:
        """
        Resolve EVENT_ID to concrete OMOP row.
        Cached per-instance.
        """
        session = so.object_session(self)
        cls = self.resolved_event_class
        if session is None or cls is None:
            return None

        return session.get(cls, self.event_id)

    def __repr__(self):
        target = self.resolved_event
        if target is not None:
            return (
                f"<EpisodeEvent ep={self.episode_id} "
                f"{target.__class__.__name__}#{self.event_id}>"
            )
        return f"<EpisodeEvent ep={self.episode_id} event={self.event_id}>"
    
    @property
    def episode_start_datetime(self):
        return (
            self.episode.episode_start_datetime
            if self.episode else None
        )
    
    @property
    def resolved_event_id_column(self) -> str | None:
        """
        Name of the ID column on the resolved event table.

        Derived from episode_event_field_concept_id metadata.
        
        Example:
            'condition_occurrence.condition_occurrence_id' resolves to 'condition_occurrence_id'
        """
        if self.event_field and "." in self.event_field.concept_name:
            return self.event_field.concept_name.split(".", 1)[1]
        return None