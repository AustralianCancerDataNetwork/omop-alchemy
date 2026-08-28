"""SQLAlchemy projections for a consistent cross-table clinical-event shape."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from omop_alchemy.cdm.base import ModifierTargetMixin

from .contracts import ClinicalEventColumn


class UnsupportedClinicalEventModelError(TypeError):
    """Raised when a model cannot provide a canonical clinical-event projection."""

    def __init__(self, model: object, reason: str) -> None:
        self.model = model
        self.reason = reason
        name = getattr(model, "__name__", repr(model))
        super().__init__(f"{name} is not a supported clinical-event model: {reason}")


@dataclass(frozen=True, slots=True)
class ClinicalEventModelSpec:
    """Resolved model metadata used to build a canonical event projection."""

    event_id_column: str
    event_concept_id_column: str
    event_date_column: str
    event_datetime_column: str | None
    event_field_concept_id: int
    event_source_table: str


def _subclasses(model: type[Any]) -> Iterator[type[Any]]:
    for subclass in model.__subclasses__():
        yield subclass
        yield from _subclasses(subclass)


def _metadata_candidate(model: type[Any]) -> type[ModifierTargetMixin] | None:
    candidates = [model, *_subclasses(model)]
    supported: list[type[ModifierTargetMixin]] = []
    for candidate in candidates:
        if not issubclass(candidate, ModifierTargetMixin):
            continue
        required_names = (
            "__event_id_col__",
            "__concept_id_col__",
            "__start_date_col__",
        )
        if any(not getattr(candidate, name, None) for name in required_names):
            continue
        try:
            candidate.modifier_field_concept_id()
        except NotImplementedError:
            continue
        supported.append(candidate)

    if not supported:
        return None
    supported.sort(
        key=lambda candidate: (
            candidate is not model,
            -len(candidate.mro()),
            f"{candidate.__module__}.{candidate.__qualname__}",
        )
    )
    return supported[0]


def _datetime_column_name(model: type[Any], date_column_name: str) -> str | None:
    if date_column_name.endswith("_date"):
        candidate = f"{date_column_name[:-5]}_datetime"
        if hasattr(model, candidate):
            return candidate
    return None


def clinical_event_model_spec(model: type[Any]) -> ClinicalEventModelSpec:
    """Resolve the event metadata for an ORM model without accessing a database."""
    if not isinstance(model, type) or not hasattr(model, "__table__"):
        raise UnsupportedClinicalEventModelError(model, "expected a mapped ORM model class")

    metadata_model = _metadata_candidate(model)
    if metadata_model is None:
        raise UnsupportedClinicalEventModelError(
            model,
            "no complete ModifierTargetMixin metadata is available",
        )

    event_id_column = metadata_model.__event_id_col__
    event_concept_id_column = metadata_model.__concept_id_col__
    event_date_column = metadata_model.__start_date_col__
    required_columns = (
        event_id_column,
        event_concept_id_column,
        event_date_column,
        "person_id",
    )
    missing = tuple(name for name in required_columns if not hasattr(model, name))
    if missing:
        raise UnsupportedClinicalEventModelError(
            model,
            f"missing required columns: {', '.join(missing)}",
        )

    try:
        field_concept_id = metadata_model.modifier_field_concept_id()
    except NotImplementedError as error:
        raise UnsupportedClinicalEventModelError(
            model,
            "modifier Field concept is not defined",
        ) from error

    return ClinicalEventModelSpec(
        event_id_column=event_id_column,
        event_concept_id_column=event_concept_id_column,
        event_date_column=event_date_column,
        event_datetime_column=_datetime_column_name(model, event_date_column),
        event_field_concept_id=field_concept_id,
        event_source_table=metadata_model.modifier_target_table(),
    )


def _nullable_column(
    model: type[Any],
    name: ClinicalEventColumn,
    sql_type: sa.types.TypeEngine[Any],
) -> sa.ColumnElement[Any]:
    column = getattr(model, str(name), None)
    if column is None:
        return sa.cast(sa.null(), sql_type).label(str(name))
    return column.label(str(name))


def canonical_event_projection(
    model: type[Any],
    *,
    include_values: bool = True,
) -> sa.Select[Any]:
    """Project one supported OMOP event model to canonical event columns."""
    spec = clinical_event_model_spec(model)
    event_datetime = (
        getattr(model, spec.event_datetime_column)
        if spec.event_datetime_column is not None
        else sa.cast(sa.null(), sa.DateTime)
    )
    columns: list[sa.ColumnElement[Any]] = [
        model.person_id.label(str(ClinicalEventColumn.person_id)),
        getattr(model, spec.event_id_column).label(str(ClinicalEventColumn.event_id)),
        getattr(model, spec.event_date_column).label(str(ClinicalEventColumn.event_date)),
        event_datetime.label(str(ClinicalEventColumn.event_datetime)),
        getattr(model, spec.event_concept_id_column).label(
            str(ClinicalEventColumn.event_concept_id)
        ),
        sa.literal(spec.event_field_concept_id).label(
            str(ClinicalEventColumn.event_field_concept_id)
        ),
        sa.literal(spec.event_source_table).label(
            str(ClinicalEventColumn.event_source_table)
        ),
    ]
    if include_values:
        columns.extend(
            (
                _nullable_column(model, ClinicalEventColumn.value_as_number, sa.Float()),
                _nullable_column(
                    model,
                    ClinicalEventColumn.value_as_concept_id,
                    sa.Integer(),
                ),
                _nullable_column(model, ClinicalEventColumn.unit_concept_id, sa.Integer()),
            )
        )
    return sa.select(*columns)


def canonical_event_union(
    *models: type[Any],
    include_values: bool = True,
) -> sa.Select[Any] | sa.CompoundSelect[Any]:
    """Combine supported event models into one canonical ``UNION ALL`` query."""
    if not models:
        raise ValueError("canonical_event_union requires at least one model")
    projections = [
        canonical_event_projection(model, include_values=include_values)
        for model in models
    ]
    if len(projections) == 1:
        return projections[0]
    return sa.union_all(*projections)
