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

    This is a declarative marker, not a support list. Which models are
    accepted as modifier sources stays an explicit allow-list in the toolkit;
    wearing this mixin describes a model's shape, it does not enrol it.

    The mixin exposes row-level link columns and properties only. Bulk target
    validation, including person and Field-concept checks, is provided by the
    toolkit's ``modifier_target_queries`` builder rather than by an implicit
    ``resolved_target`` lookup on each ORM instance.
    """

    __abstract__ = True
    __tablename__: ClassVar[str]
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
    """

    __abstract__ = True
    __tablename__: ClassVar[str]
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
