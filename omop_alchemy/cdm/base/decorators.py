from typing import TypeVar

from oa_configurator import validate_schema_tag

from .cdm_table_base import CDMTableBase

T = TypeVar("T", bound=type)
MODEL_MODULE_PREFIX = "omop_alchemy.cdm.model."

def _infer_table_category(cls: type) -> str | None:
    model_module = cls.__module__
    if not model_module.startswith(MODEL_MODULE_PREFIX):
        return None
    suffix = model_module.removeprefix(MODEL_MODULE_PREFIX)
    return suffix.split(".", 1)[0]

def cdm_table(cls: T) -> T:
    """
    Mark a SQLAlchemy declarative class as a concrete OMOP CDM table.

    - Forces __abstract__ = False
    - Ensures __tablename__ is defined
    - Inherits from CDMTableBase
    - Validates the table's own schema tag (see validate_schema_tag)
    - Used to clearly distinguish real CDM tables from mixins
    """

    # Safety: require an explicit tablename
    if not hasattr(cls, "__tablename__"):
        raise TypeError(
            f"@cdm_table applied to {cls.__name__} "
            "but __tablename__ is not defined"
        )

    if not issubclass(cls, CDMTableBase):
        raise TypeError(
            f"{cls.__name__} must inherit from CDMTableBase "
        )

    try:
        schema_tag = validate_schema_tag(cls.__table__)  # ty: ignore[unresolved-attribute]
    except ValueError as exc:
        raise TypeError(f"@cdm_table on {cls.__name__}: {exc}") from exc
    if schema_tag is None:
        raise TypeError(
            f"@cdm_table on {cls.__name__}: table has no schema tag. "
            "Every CDM table must declare __table_args__['schema']."
        )

    # Explicitly mark as concrete
    cls.__abstract__ = False
    cls.__omop_is_cdm_table__ = True
    cls.__omop_table_category__ = _infer_table_category(cls)

    return cls
