from sqlalchemy.orm import declared_attr

from ...config import CDM_EXTENSION_SCHEMA
from .cdm_table_base import CDMTableBase


class CDMExtensionTableBase(CDMTableBase):
    """Base for third-party tables extending the CDM.
    Defaults __table_args__ to the extension schema and tags every
    descendant as extension-owned via _is_extension. Every descendant is
    locked to the extension schema: a subclass cannot opt into a different
    one via its own __table_args__.
    """

    __abstract__ = True

    @declared_attr.directive
    def __table_args__(cls):
        return {"schema": CDM_EXTENSION_SCHEMA}

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(_is_extension=True, **kwargs)
        if cls.__omop_is_cdm_table__ and cls.__table__.schema != CDM_EXTENSION_SCHEMA:
            raise TypeError(
                f"{cls.__name__} must live in the {CDM_EXTENSION_SCHEMA!r} schema; "
                "extension tables cannot override their schema via __table_args__."
            )
