from __future__ import annotations
from sqlalchemy.ext.hybrid import hybrid_property
from typing import ClassVar,Optional
from datetime import date
from sqlalchemy.sql.elements import SQLColumnExpression

class ModifierSourceMixin:
    """
    Marker and helpers for OMOP tables that record a supplementary fact about
    another CDM row through OMOP's polymorphic modifier link.

    OMOP puts the modifier link on the source table under table-specific
    column names (``measurement_event_id`` / ``meas_event_field_concept_id``
    versus ``observation_event_id`` / ``obs_event_field_concept_id``).
    Subclasses name those columns once and inherit a common vocabulary, so
    query code never branches on the physical modifier source.

    Built-in CDM tables put this mixin on the bare Measurement and Observation
    classes. Custom mapped sources may also use it when they supply complete
    event and link metadata; source support is validated from that metadata,
    not restricted to the built-in cached specs. Wearing this mixin does not
    enrol a model in the clinical-event or modifier-target registries.

    The mixin exposes row-level link columns and properties only. Bulk target
    validation, including person and Field-concept checks, is provided by the
    toolkit's ``modifier_target_queries`` builder rather than by an implicit
    ``resolved_target`` lookup on each ORM instance.

    This asymmetry is deliberate: Episode_EventView retains a session-bound
    convenience for navigating one existing link, without person validation.
    Measurement and Observation instead use set-based target queries for bulk
    processing and person/Field/event validation, avoiding implicit per-row
    target loads on fact-table instances.
    """

    __abstract__ = True
    __tablename__: ClassVar[str]
    # Source-link metadata names the target identity, not this row's own ID.
    __modifier_event_id_col__: ClassVar[str]
    __modifier_field_concept_id_col__: ClassVar[str]

    @classmethod
    def modifier_source_table(cls) -> str:
        return cls.__tablename__

    @hybrid_property
    def modifier_of_event_id(self) -> Optional[int]:
        return getattr(self, self.__modifier_event_id_col__)

    @modifier_of_event_id.inplace.expression
    @classmethod
    def _modifier_of_event_id(cls) -> SQLColumnExpression[Optional[int]]:
        return getattr(cls, cls.__modifier_event_id_col__)

    @hybrid_property
    def modifier_of_field_concept_id(self) -> Optional[int]:
        return getattr(self, self.__modifier_field_concept_id_col__)

    @modifier_of_field_concept_id.inplace.expression
    @classmethod
    def _modifier_of_field_concept_id(cls) -> SQLColumnExpression[Optional[int]]:
        return getattr(cls, cls.__modifier_field_concept_id_col__)


class ModifierTargetMixin:
    """
    Marker and helpers for OMOP tables that can receive a polymorphic modifier
    link from Measurements, Observations, or Episode Events.

    Wearing this mixin describes target-row identity metadata; it does not
    enrol a class as a clinical event or modifier target in a registry.

    Built-in CDM models place it on analytical Views, keeping the bare tables
    lean. That placement convention does not restrict custom mapped sources
    from using both source and target metadata without a View/context base.
    """

    __abstract__ = True
    __tablename__: ClassVar[str]
    # Target-self metadata names this row's identity and clinical fields.
    __event_id_col__: ClassVar[str]
    __concept_id_col__: ClassVar[str]
    __start_date_col__: ClassVar[str]
    __end_date_col__: ClassVar[str]
    __type_concept_id_col__: ClassVar[str]

    @classmethod
    def modifier_field_concept_id(cls) -> int:
        raise NotImplementedError

    @classmethod
    def modifier_target_table(cls) -> str:
        return cls.__tablename__ 

    @hybrid_property
    def event_id(self) -> int:
        return getattr(self, self.__event_id_col__)
    
        
    @event_id.inplace.expression
    @classmethod
    def _event_id(cls) -> SQLColumnExpression[int]:
        return getattr(cls, cls.__event_id_col__)
    
    @property
    def concept_id(self) -> int:
        return getattr(self, self.__concept_id_col__)

    @property
    def start_date(self) -> date:
        return getattr(self, self.__start_date_col__)

    @property
    def end_date(self) -> Optional[date]:
        return getattr(self, self.__end_date_col__)

    @property
    def type_concept_id(self) -> int:
        return getattr(self, self.__type_concept_id_col__)
