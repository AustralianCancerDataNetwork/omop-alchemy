from __future__ import annotations

from orm_loader.helpers import Base
from orm_loader.tables import CSVLoadableTableInterface, SerialisableTableInterface
import sqlalchemy as sa
import sqlalchemy.orm as so

MODEL_MODULE_PREFIX = "omop_alchemy.cdm.model."


def _infer_table_category(cls: type) -> str | None:
    """Category inferred from the module path under omop_alchemy.cdm.model; None outside it."""
    model_module = cls.__module__
    if not model_module.startswith(MODEL_MODULE_PREFIX):
        return None
    suffix = model_module.removeprefix(MODEL_MODULE_PREFIX)
    return suffix.split(".", 1)[0]


def _redeclares_a_tagged_ancestors_table(cls: type) -> bool:
    """Whether cls's own __tablename__ just repeats an already-tagged
    ancestor's, rather than declaring a genuinely new table.

    Some subclasses redeclare __tablename__/__table_args__ purely to work
    around SQLAlchemy quirks (e.g. an abstract mixin elsewhere in the MRO
    that would otherwise make it build a second, unlinked Table object),
    while still representing the very same physical table as that ancestor.
    """
    own_tablename = cls.__dict__.get("__tablename__")
    return any(
        base.__dict__.get("__omop_is_cdm_table__") is True
        and base.__dict__.get("__tablename__") == own_tablename
        for base in cls.__mro__[1:]
    )


class CDMTableBase(CSVLoadableTableInterface, SerialisableTableInterface, Base):
    """Base for CDM tables. Automatically tags every concrete subclass,
    inferring its maintenance category from the module path under omop_alchemy.cdm.model.

    Notes
    -----
    Bakes in the shared Base, so every subclass registers permanently on the
    process-wide registry. SQLAlchemy has no reliable way to fully unmap a
    declarative class from within the same process, so a test-only subclass
    can't be disposed of outright: instead, set its own
    __omop_is_cdm_table__ = False (the flag every maintenance scan gates on)
    and remove its table from Base.metadata, leaving the class/mapper behind
    as an inert orphan. See tests/test_cdm_table_base.py for the pattern.
    """

    __abstract__ = True
    __omop_is_cdm_table__ = False
    __omop_table_category__: str | None = None
    __omop_extension_owner__: str | None = None
    __cdm_extra_checks__: list[str] = []

    def __init_subclass__(cls, *, _is_extension: bool = False, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)

        # Reset on cls's own __dict__ first, so a subclass never inherits True by attribute lookup.
        cls.__omop_is_cdm_table__ = False
        cls.__omop_table_category__ = None
        cls.__omop_extension_owner__ = None

        if cls.__dict__.get("__abstract__", False):
            return

        if "__tablename__" not in cls.__dict__ or _redeclares_a_tagged_ancestors_table(cls):
            # Not a genuinely new table (a view/mixin subclass, or one redeclaring
            # __tablename__ for an unrelated reason). Stays untagged, set above.
            return

        is_internal_module = cls.__module__.startswith(MODEL_MODULE_PREFIX)
        if not _is_extension and not is_internal_module:
            raise TypeError(
                f"{cls.__name__} in module {cls.__module__!r} does not live under "
                f"{MODEL_MODULE_PREFIX!r}; third-party tables should inherit "
                "CDMExtensionTableBase instead."
            )
        if _is_extension and is_internal_module:
            raise TypeError(
                f"{cls.__name__} is defined inside {MODEL_MODULE_PREFIX!r} "
                "and should inherit CDMTableBase directly instead of CDMExtensionTableBase."
            )

        schema_tag = cls.__table__.schema
        if schema_tag is None:
            raise TypeError(f"{cls.__name__}: table has no schema tag.")

        if _is_extension:
            cls.__omop_extension_owner__ = cls.__module__.split(".", 1)[0]
            cls.__omop_table_category__ = None
        else:
            cls.__omop_table_category__ = _infer_table_category(cls)
            cls.__omop_extension_owner__ = None

        cls.__omop_is_cdm_table__ = True

    @classmethod
    def table_has_rows(cls, session: so.Session) -> bool:
        return session.execute(
            sa.select(sa.literal(True)).select_from(cls).limit(1)
        ).scalar() is not None
