"""Validate canonical modifier links against supported OMOP target tables."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.sql.selectable import FromClause, SelectBase

from omop_alchemy.toolkit._utils import _as_from_clause, _require_columns
from omop_alchemy.toolkit.core.events import ClinicalEventColumn

from .contracts import (
    ModifierColumn,
    ModifierTargetDiagnosticCode,
    ModifierTargetDiagnosticColumn,
)
from .metadata import modifier_target_model_spec
from .projections import canonical_modifier_projection

RESOLVED_TARGET_PERSON_ID = "resolved_target_person_id"
RESOLVED_TARGET_EVENT_ID = "resolved_target_event_id"
RESOLVED_TARGET_FIELD_CONCEPT_ID = "resolved_target_field_concept_id"
RESOLVED_TARGET_SOURCE_TABLE = "resolved_target_source_table"


class InvalidModifierTargetSourceError(ValueError):
    """Raised when a supplied projection lacks canonical columns."""


@dataclass(frozen=True, slots=True)
class ModifierTargetQueries:
    matches: sa.Select[Any]
    diagnostics: SelectBase | None = None


def canonical_modifier_target_projection(model: type[Any]) -> sa.Select[Any]:
    """Project a supported clinical event or Episode to target identity columns."""
    spec = modifier_target_model_spec(model)
    return sa.select(
        model.person_id.label(str(ClinicalEventColumn.person_id)),
        getattr(model, spec.event_id_attribute).label(
            str(ClinicalEventColumn.event_id)
        ),
        sa.literal(spec.event_field_concept_id).label(
            str(ClinicalEventColumn.event_field_concept_id)
        ),
        sa.literal(spec.event_source_table).label(
            str(ClinicalEventColumn.event_source_table)
        ),
    )


def modifier_target_queries(
    modifier_source: type[Any] | FromClause | SelectBase,
    target_source: type[Any] | FromClause | SelectBase,
    *,
    include_unmatched: bool = False,
    diagnostics: bool = False,
) -> ModifierTargetQueries:
    """Resolve valid target links and optionally explain rejected modifier rows.

    Parameters
    ----------
    modifier_source:
        A supported modifier model or a selectable exposing the canonical
        modifier columns.
    target_source:
        A supported ORM target model or a selectable exposing target identity
        columns. Selectables are treated as the caller's supplied scope.
    include_unmatched:
        If ``True``, retain modifier rows without a valid target in
        ``matches``. Otherwise only valid links are returned.
    diagnostics:
        If ``True``, also build an advisory diagnostic selectable. Building
        the queries does not execute either selectable.

    Returns
    -------
    ModifierTargetQueries
        ``matches`` contains the modifier columns plus resolved target
        identity columns. ``diagnostics`` is ``None`` unless requested.

    Notes
    -----
    A link is valid only when target event ID, target Field concept and person
    ID all agree. For an ORM target model, diagnostics can report an
    unsupported target field or a missing target event. For a caller-supplied
    selectable, those absences may be caused by filtering, so only missing
    identity and observed person mismatches are reported.
    """
    modifiers = (
        canonical_modifier_projection(modifier_source).subquery("target_modifiers")
        if isinstance(modifier_source, type)
        else _as_from_clause(modifier_source, name="target_modifiers")
    )
    target_spec = (
        modifier_target_model_spec(target_source)
        if isinstance(target_source, type)
        else None
    )
    targets = (
        canonical_modifier_target_projection(target_source).subquery("modifier_targets")
        if isinstance(target_source, type)
        else _as_from_clause(target_source, name="modifier_targets")
    )
    _require_columns(
        modifiers.c.keys(),
        tuple(str(column) for column in ModifierColumn.required_columns()),
        role="modifier source",
        error_type=InvalidModifierTargetSourceError,
    )
    _require_columns(
        targets.c.keys(),
        tuple(
            str(column)
            for column in (
                ClinicalEventColumn.person_id,
                ClinicalEventColumn.event_id,
                ClinicalEventColumn.event_field_concept_id,
                ClinicalEventColumn.event_source_table,
            )
        ),
        role="target source",
        error_type=InvalidModifierTargetSourceError,
    )

    person = str(ModifierColumn.person_id)
    event_id = str(ClinicalEventColumn.event_id)
    event_field = str(ClinicalEventColumn.event_field_concept_id)
    target_id = str(ModifierColumn.target_event_id)
    target_field = str(ModifierColumn.target_field_concept_id)
    valid_link = sa.and_(
        modifiers.c[target_id] == targets.c[event_id],
        modifiers.c[target_field] == targets.c[event_field],
        modifiers.c[person] == targets.c[person],
    )
    joined = modifiers.join(targets, valid_link, isouter=include_unmatched)
    matches = sa.select(
        *modifiers.c,
        targets.c[person].label(RESOLVED_TARGET_PERSON_ID),
        targets.c[event_id].label(RESOLVED_TARGET_EVENT_ID),
        targets.c[event_field].label(RESOLVED_TARGET_FIELD_CONCEPT_ID),
        targets.c[str(ClinicalEventColumn.event_source_table)].label(
            RESOLVED_TARGET_SOURCE_TABLE
        ),
    ).select_from(joined)

    if not diagnostics:
        return ModifierTargetQueries(matches=matches)

    has_identity = sa.and_(
        modifiers.c[target_id].is_not(None), modifiers.c[target_field].is_not(None)
    )
    any_event = sa.exists(
        sa.select(1)
        .select_from(targets)
        .where(
            targets.c[event_id] == modifiers.c[target_id],
            targets.c[event_field] == modifiers.c[target_field],
        )
    )
    same_person = sa.exists(sa.select(1).select_from(targets).where(valid_link))

    def branch(
        code: ModifierTargetDiagnosticCode,
        condition: sa.ColumnElement[bool],
        message: str,
    ) -> sa.Select[Any]:
        return sa.select(
            sa.literal(str(code)).label(
                str(ModifierTargetDiagnosticColumn.diagnostic_code)
            ),
            modifiers.c[str(ModifierColumn.modifier_source_table)],
            modifiers.c[str(ModifierColumn.modifier_id)],
            modifiers.c[target_field],
            modifiers.c[target_id],
            sa.literal(message).label(str(ModifierTargetDiagnosticColumn.message)),
        ).where(condition)

    branches: list[sa.Select[Any]] = [
        branch(
            ModifierTargetDiagnosticCode.missing_target_identity,
            sa.not_(has_identity),
            "modifier does not identify both a target row and target field",
        )
    ]
    if target_spec is not None:
        supported = modifiers.c[target_field] == target_spec.event_field_concept_id
        branches.append(
            branch(
                ModifierTargetDiagnosticCode.unsupported_target_field,
                sa.and_(has_identity, sa.not_(supported)),
                "target field concept is not supported by the supplied target",
            )
        )
        # Absence from the target set proves the row does not exist only when
        # the set is a whole target table. A caller-supplied selectable may be
        # filtered, where a missing row is the filter's doing, not a defect.
        branches.append(
            branch(
                ModifierTargetDiagnosticCode.missing_target_event,
                sa.and_(has_identity, supported, sa.not_(any_event)),
                "the identified target event does not exist",
            )
        )
    else:
        supported = sa.true()
    # Safe for either source: the mismatch is observed on a row that is present.
    branches.append(
        branch(
            ModifierTargetDiagnosticCode.person_mismatch,
            sa.and_(has_identity, supported, any_event, sa.not_(same_person)),
            "modifier and target event belong to different people",
        )
    )
    return ModifierTargetQueries(matches=matches, diagnostics=sa.union_all(*branches))
