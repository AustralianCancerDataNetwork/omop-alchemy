"""Oncology policies for condition modifiers and preferred stage values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.sql.selectable import FromClause, SelectBase

from omop_alchemy.toolkit._utils import _as_from_clause
from omop_alchemy.toolkit.core.modifiers import (
    ModifierSelectionPolicy,
    ModifierSelectionSpec,
    selected_modifier_select,
)


class StageBasis(StrEnum):
    pathological = "pathological"
    clinical = "clinical"
    unclassified = "unclassified"


@dataclass(frozen=True, slots=True)
class StageSelectionSpec:
    """Preferred stage basis followed by temporal tie-breaking policy.

    The default prefers pathological stage, then clinical stage, then values
    whose vocabulary code does not identify a basis. Supply an empty
    ``basis_priority`` for chronological-only selection.
    """

    basis_priority: tuple[StageBasis, ...] = (
        StageBasis.pathological,
        StageBasis.clinical,
        StageBasis.unclassified,
    )
    temporal_policy: ModifierSelectionPolicy = ModifierSelectionPolicy.earliest

    def __post_init__(self) -> None:
        expected = set(StageBasis)
        if self.basis_priority and (
            len(self.basis_priority) != len(expected)
            or set(self.basis_priority) != expected
        ):
            raise ValueError(
                "basis_priority must be empty or contain each StageBasis exactly once"
            )

    @classmethod
    def clinical_first(
        cls,
        *,
        temporal_policy: ModifierSelectionPolicy = ModifierSelectionPolicy.earliest,
    ) -> StageSelectionSpec:
        return cls(
            basis_priority=(
                StageBasis.clinical,
                StageBasis.pathological,
                StageBasis.unclassified,
            ),
            temporal_policy=temporal_policy,
        )

    @classmethod
    def chronological_only(
        cls,
        *,
        temporal_policy: ModifierSelectionPolicy = ModifierSelectionPolicy.earliest,
    ) -> StageSelectionSpec:
        return cls(
            basis_priority=(),
            temporal_policy=temporal_policy,
        )


DEFAULT_STAGE_SELECTION = StageSelectionSpec()


def stage_basis_expression(
    concept_code: sa.ColumnElement[Any],
) -> sa.ColumnElement[str]:
    """Classify stage codes using the source vocabulary's p/c convention.

    The staging vocabulary does not expose a reliable parent concept that
    separates pathological from clinical staging. The code prefix is therefore
    the intentional source-level contract rather than a synthetic hierarchy.
    """
    normalized = sa.func.lower(sa.func.trim(concept_code))
    return sa.case(
        (normalized.like("p%"), str(StageBasis.pathological)),
        (normalized.like("c%"), str(StageBasis.clinical)),
        else_=str(StageBasis.unclassified),
    )


def stage_basis_priority_expression(
    concept_code: sa.ColumnElement[Any],
    spec: StageSelectionSpec = DEFAULT_STAGE_SELECTION,
) -> sa.ColumnElement[int]:
    """Render the configured stage-basis preference as a sortable SQL CASE."""
    if not spec.basis_priority:
        return sa.literal(0)
    basis = stage_basis_expression(concept_code)
    return sa.case(
        *(
            (basis == str(candidate), rank)
            for rank, candidate in enumerate(spec.basis_priority)
        ),
        else_=len(spec.basis_priority),
    )


def preferred_stage_select(
    source: FromClause | SelectBase,
    *,
    spec: StageSelectionSpec = DEFAULT_STAGE_SELECTION,
    concept_code_column: str | None = None,
) -> sa.Select[Any]:
    """Select one preferred stage modifier for every canonical target.

    Basis-ranked selection requires a source enriched with a concept-code
    column. Chronological-only selection does not require that enrichment.
    """
    modifiers = _as_from_clause(source, name="preferred_stage_source")
    priority: tuple[sa.ColumnElement[Any], ...] = ()
    if spec.basis_priority:
        if concept_code_column is None:
            raise ValueError(
                "concept_code_column is required when basis ranking is enabled"
            )
        if not concept_code_column.strip():
            raise ValueError("concept_code_column must not be empty")
        if concept_code_column not in modifiers.c:
            raise ValueError(
                "stage source is missing required concept-code column: "
                f"{concept_code_column}"
            )
        priority = (
            stage_basis_priority_expression(
                modifiers.c[concept_code_column], spec
            ).asc(),
        )
    return selected_modifier_select(
        modifiers,
        spec=ModifierSelectionSpec(policy=spec.temporal_policy),
        priority=priority,
    )
