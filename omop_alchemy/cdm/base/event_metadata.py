"""Database-free event metadata shapes and capability checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import UnsupportedModelError
from .modifier_interface import ModifierTargetMixin


class UnsupportedClinicalEventModelError(UnsupportedModelError):
    """Raised when a model cannot provide a canonical clinical-event projection."""

    model_kind = "clinical-event model"


@dataclass(frozen=True, slots=True)
class ClinicalEventModelSpec:
    """Resolved model metadata used to build a canonical event projection."""

    event_id_column: str
    event_concept_id_column: str
    event_date_column: str
    event_datetime_column: str | None
    event_field_concept_id: int
    event_source_table: str
    event_end_date_column: str | None = None
    event_end_datetime_column: str | None = None


def _has_complete_event_metadata(model: type[Any]) -> bool:
    # A model is eligible to own metadata only when the complete modifier
    # contract is present. Partial class attributes would produce a projection
    # whose labels look valid while pointing at the wrong source columns.
    if not issubclass(model, ModifierTargetMixin):
        return False
    if any(
        not getattr(model, name, None)
        for name in ("__event_id_col__", "__concept_id_col__", "__start_date_col__")
    ):
        return False
    try:
        model.modifier_field_concept_id()
    except NotImplementedError:
        return False
    return True


def _datetime_column_name(model: type[Any], date_column_name: str) -> str | None:
    """Return the conventional datetime counterpart only when exposed."""
    if date_column_name.endswith("_date"):
        candidate = f"{date_column_name[:-5]}_datetime"
        if hasattr(model, candidate):
            return candidate
    return None


def _interval_columns(
    model: type[Any], metadata_model: type[ModifierTargetMixin]
) -> tuple[str | None, str | None]:
    """Resolve independent endpoints; a start-date alias denotes a point."""
    end_date = getattr(metadata_model, "__end_date_col__", None)
    if (
        not isinstance(end_date, str)
        or end_date == metadata_model.__start_date_col__
        or not hasattr(model, end_date)
    ):
        return None, None
    return end_date, _datetime_column_name(model, end_date)
