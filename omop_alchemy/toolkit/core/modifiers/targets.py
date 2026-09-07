"""Validate canonical modifier links against supported OMOP target tables."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.sql.selectable import FromClause, SelectBase

from omop_alchemy.toolkit.core.events import ClinicalEventColumn

from .contracts import (
    CANONICAL_MODIFIER_REQUIRED_COLUMNS,
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


def _as_source(source: FromClause | SelectBase, name: str) -> FromClause:
    if isinstance(source, SelectBase):
        return source.subquery(name)
    if isinstance(source, FromClause):
        return source
    raise TypeError(f"{name} must be a SQLAlchemy Select or FromClause")


def _require(source: FromClause, columns: tuple[str, ...], role: str) -> None:
    missing = tuple(name for name in columns if name not in source.c)
    if missing:
        raise InvalidModifierTargetSourceError(
            f"{role} is missing required columns: {', '.join(missing)}"
        )


def modifier_target_queries(
    modifier_source: type[Any] | FromClause | SelectBase,
    target_source: type[Any] | FromClause | SelectBase,
    *,
    include_unmatched: bool = False,
    diagnostics: bool = False,
) -> ModifierTargetQueries:
    """Resolve valid target links and optionally explain rejected modifier rows."""
    modifiers = (
        canonical_modifier_projection(modifier_source).subquery("target_modifiers")
        if isinstance(modifier_source, type)
        else _as_source(modifier_source, "target_modifiers")
    )
    target_spec = (
        modifier_target_model_spec(target_source)
        if isinstance(target_source, type)
        else None
    )
    targets = (
        canonical_modifier_target_projection(target_source).subquery("modifier_targets")
        if isinstance(target_source, type)
        else _as_source(target_source, "modifier_targets")
    )
    _require(
        modifiers,
        tuple(str(column) for column in CANONICAL_MODIFIER_REQUIRED_COLUMNS),
        "modifier source",
    )
    _require(
        targets,
        tuple(
            str(column)
            for column in (
                ClinicalEventColumn.person_id,
                ClinicalEventColumn.event_id,
                ClinicalEventColumn.event_field_concept_id,
                ClinicalEventColumn.event_source_table,
            )
        ),
        "target source",
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
    else:
        supported = sa.true()
    branches.extend(
        (
            branch(
                ModifierTargetDiagnosticCode.missing_target_event,
                sa.and_(has_identity, supported, sa.not_(any_event)),
                "the identified target event does not exist",
            ),
            branch(
                ModifierTargetDiagnosticCode.person_mismatch,
                sa.and_(has_identity, supported, any_event, sa.not_(same_person)),
                "modifier and target event belong to different people",
            ),
        )
    )
    return ModifierTargetQueries(matches=matches, diagnostics=sa.union_all(*branches))
