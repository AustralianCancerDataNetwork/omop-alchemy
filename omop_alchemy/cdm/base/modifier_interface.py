from __future__ import annotations
from sqlalchemy.ext.hybrid import hybrid_property
from typing import ClassVar,Optional
from datetime import date
from sqlalchemy.sql.elements import SQLColumnExpression

class ModifierTargetMixin:
    """
    Marker + helpers for OMOP tables that can be modified
    by Measurements / Observations / Episode Events.
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