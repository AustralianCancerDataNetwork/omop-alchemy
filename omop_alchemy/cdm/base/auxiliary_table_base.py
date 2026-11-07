from sqlalchemy.orm import declared_attr

from ...config import CDM_AUXILIARY_SCHEMA
from .cdm_table_base import CDMTableBase


class CDMAuxiliaryTableBase(CDMTableBase):
    """Base for third-party tables extending the CDM.
    Defaults __table_args__ to the auxiliary schema and tags every
    descendant as auxiliary-owned via _is_auxiliary. Every descendant is
    locked to the auxiliary schema: a subclass cannot opt into a different
    one via its own __table_args__.
    """

    __abstract__ = True

    @declared_attr.directive
    def __table_args__(cls):
        return {"schema": CDM_AUXILIARY_SCHEMA}

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(_is_auxiliary=True, **kwargs)
        if cls.__omop_is_cdm_table__ and cls.__table__.schema != CDM_AUXILIARY_SCHEMA:
            raise TypeError(
                f"{cls.__name__} must live in the {CDM_AUXILIARY_SCHEMA!r} schema; "
                "auxiliary tables cannot override their schema via __table_args__."
            )
