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


class ClinicalEventMixin(ModifierTargetMixin):
    """CDM event metadata and helpers shared by clinical analytical Views.

    Inheritance provides a metadata capability, not registry membership. Custom
    modifier sources may use this mixin without becoming episode-event targets.
    The model registry selects supported events; toolkit builds SQL from specs.
    """

    @classmethod
    def has_complete_metadata(cls) -> bool:
        """Whether event identity, concept, date and Field metadata are declared."""
        if any(
            not getattr(cls, name, None)
            for name in ("__event_id_col__", "__concept_id_col__", "__start_date_col__")
        ):
            return False
        try:
            cls.modifier_field_concept_id()
        except NotImplementedError:
            return False
        return True

    @classmethod
    def clinical_event_model_spec(
        cls, source_model: type[Any] | None = None
    ) -> ClinicalEventModelSpec:
        """Validate event metadata against this View or a bare source model.

        This method accesses no database or registry. A registered View can
        supply declarations while a bare table supplies the physical columns.
        """
        model = cls if source_model is None else source_model
        if not isinstance(model, type) or not hasattr(model, "__table__"):
            raise UnsupportedClinicalEventModelError(
                model, "expected a mapped ORM model class"
            )
        if not cls.has_complete_metadata():
            raise UnsupportedClinicalEventModelError(
                model, "no complete ClinicalEventMixin metadata is available"
            )

        event_id_column = cls.__event_id_col__
        event_concept_id_column = cls.__concept_id_col__
        event_date_column = cls.__start_date_col__
        required_columns = (
            event_id_column,
            event_concept_id_column,
            event_date_column,
            "person_id",
        )
        missing = tuple(name for name in required_columns if not hasattr(model, name))
        if missing:
            raise UnsupportedClinicalEventModelError(
                model, f"missing required columns: {', '.join(missing)}"
            )

        end_date, end_datetime = cls._interval_columns(model)
        return ClinicalEventModelSpec(
            event_id_column=event_id_column,
            event_concept_id_column=event_concept_id_column,
            event_date_column=event_date_column,
            event_datetime_column=cls._datetime_column_name(model, event_date_column),
            event_field_concept_id=cls.modifier_field_concept_id(),
            event_source_table=cls.modifier_target_table(),
            event_end_date_column=end_date,
            event_end_datetime_column=end_datetime,
        )

    @staticmethod
    def _datetime_column_name(model: type[Any], date_column_name: str) -> str | None:
        """Return the conventional datetime counterpart only when exposed."""
        if date_column_name.endswith("_date"):
            candidate = f"{date_column_name[:-5]}_datetime"
            if hasattr(model, candidate):
                return candidate
        return None

    @classmethod
    def _interval_columns(cls, model: type[Any]) -> tuple[str | None, str | None]:
        """Resolve independent endpoints; a start-date alias denotes a point."""
        end_date = getattr(cls, "__end_date_col__", None)
        if (
            not isinstance(end_date, str)
            or end_date == cls.__start_date_col__
            or not hasattr(model, end_date)
        ):
            return None, None
        return end_date, cls._datetime_column_name(model, end_date)
