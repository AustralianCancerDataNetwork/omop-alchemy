"""SQLAlchemy projections for a consistent cross-table clinical-event shape."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from omop_alchemy.cdm.base.event_metadata import (
    ClinicalEventModelSpec as ClinicalEventModelSpec,
    UnsupportedClinicalEventModelError as UnsupportedClinicalEventModelError,
)
from omop_alchemy.cdm.model.clinical.event_metadata import (
    clinical_event_model_spec as clinical_event_model_spec,
)
from omop_alchemy.toolkit._utils import _nullable_column, _select_or_union_all

from .contracts import ClinicalEventColumn


def canonical_event_projection(
    model: type[Any],
    *,
    include_values: bool = True,
) -> sa.Select[Any]:
    """Project one supported OMOP event model to canonical event columns."""
    spec = clinical_event_model_spec(model)
    # The output deliberately uses canonical labels rather than source names;
    # downstream attachment, timeline, and union code should not branch on the
    # particular OMOP event table being projected.
    event_datetime = (
        getattr(model, spec.event_datetime_column)
        if spec.event_datetime_column is not None
        else sa.cast(sa.null(), sa.DateTime)
    )
    columns: list[sa.ColumnElement[Any]] = [
        model.person_id.label(str(ClinicalEventColumn.person_id)),
        getattr(model, spec.event_id_column).label(str(ClinicalEventColumn.event_id)),
        getattr(model, spec.event_date_column).label(
            str(ClinicalEventColumn.event_date)
        ),
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
        # Value fields are optional but occupy fixed positions when requested,
        # allowing heterogeneous event projections to be combined with UNION ALL.
        columns.extend(
            (
                _nullable_column(
                    model, ClinicalEventColumn.value_as_number, sa.Float()
                ),
                _nullable_column(
                    model,
                    ClinicalEventColumn.value_as_concept_id,
                    sa.Integer(),
                ),
                _nullable_column(
                    model, ClinicalEventColumn.unit_concept_id, sa.Integer()
                ),
            )
        )
    return sa.select(*columns)


def canonical_event_union(
    *models: type[Any],
    include_values: bool = True,
) -> sa.Select[Any] | sa.CompoundSelect[Any]:
    """Combine supported event models into one canonical ``UNION ALL`` query."""
    projections = [
        canonical_event_projection(model, include_values=include_values)
        for model in models
    ]
    return _select_or_union_all(
        projections,
        error_message="canonical_event_union requires at least one model",
    )
