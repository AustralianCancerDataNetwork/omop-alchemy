"""Resolve canonical fact relationships to existing clinical-event rows."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.sql.selectable import FromClause, SelectBase

from omop_alchemy.cdm.model.structural import Fact_Relationship
from omop_alchemy.toolkit._utils import (
    _as_from_clause,
    _diagnostic_literal_columns,
    _require_columns,
)
from omop_alchemy.toolkit.core.events import ClinicalEventColumn

from .contracts import (
    FactColumn,
    FactRelationshipColumn,
    FactRelationshipDiagnosticCode,
    FactRelationshipDiagnosticColumn,
    FactRelationshipQueries,
)
from .metadata import FactSource, fact_source
from .projections import InvalidFactSourceError, _fact_from_clause
from .registry import (
    FactRelationshipKind,
    FactRelationshipRegistry,
    default_fact_relationship_registry,
)

RELATIONSHIP_COLUMNS = (
    "domain_concept_id_1",
    "fact_id_1",
    "domain_concept_id_2",
    "fact_id_2",
    "relationship_concept_id",
)

ENDPOINT_COLUMNS = (
    (FactColumn.domain_concept_id, "domain_concept_id"),
    (ClinicalEventColumn.event_id, "fact_id"),
    (ClinicalEventColumn.person_id, "person_id"),
    (ClinicalEventColumn.event_date, "fact_date"),
    (ClinicalEventColumn.event_datetime, "fact_datetime"),
    (ClinicalEventColumn.event_concept_id, "fact_concept_id"),
    (ClinicalEventColumn.event_field_concept_id, "fact_field_concept_id"),
    (ClinicalEventColumn.event_source_table, "fact_source_table"),
)


def _relationship_from_clause(
    source: type[Any] | FromClause | SelectBase,
) -> FromClause:
    if isinstance(source, type):
        if not hasattr(source, "__table__"):
            raise TypeError("relationship source must be a mapped model")
        relationships = sa.select(
            *(getattr(source, column).label(column) for column in RELATIONSHIP_COLUMNS)
        ).subquery("fact_relationship_rows")
    else:
        relationships = _as_from_clause(source, name="fact_relationship_rows").alias(
            "fact_relationship_rows"
        )
    _require_columns(
        relationships.c.keys(),
        RELATIONSHIP_COLUMNS,
        role="relationship source",
        error_type=InvalidFactSourceError,
    )
    return relationships


def _endpoint_columns(
    facts: FromClause, *, side: str
) -> tuple[sa.ColumnElement[Any], ...]:
    return tuple(
        facts.c[str(source)].label(
            str(FactRelationshipColumn(f"{side}_{output_suffix}"))
        )
        for source, output_suffix in ENDPOINT_COLUMNS
    )


def fact_relationship_queries(
    from_facts: FactSource | type[Any] | FromClause | SelectBase,
    to_facts: FactSource | type[Any] | FromClause | SelectBase,
    *,
    relationship: FactRelationshipKind | str,
    registry: FactRelationshipRegistry | None = None,
    relationships: type[Any] | FromClause | SelectBase = Fact_Relationship,
    diagnostics: bool = False,
) -> FactRelationshipQueries:
    """Resolve one canonical relationship meaning between two fact scopes.

    Endpoint direction comes from ``from_facts`` and ``to_facts``. Narrowing
    either projection expresses an outbound or inbound query without a second
    direction flag. A match is returned only when both facts exist and belong
    to the same person.

    Missing-endpoint diagnostics are authoritative only for complete sources.
    ORM models are complete by default; arbitrary selectables are not. Person
    mismatch is observable and may be reported for either source kind.
    """
    active = registry or default_fact_relationship_registry()
    spec = active.resolve(relationship)
    from_source = (
        from_facts if isinstance(from_facts, FactSource) else fact_source(from_facts)
    )
    to_source = to_facts if isinstance(to_facts, FactSource) else fact_source(to_facts)
    if from_source.domain.domain_concept_id != spec.from_domain_concept_id:
        raise ValueError("from_facts belongs to the wrong Domain")
    if to_source.domain.domain_concept_id != spec.to_domain_concept_id:
        raise ValueError("to_facts belongs to the wrong Domain")

    from_rows = _fact_from_clause(from_source, name="from_facts")
    to_rows = _fact_from_clause(to_source, name="to_facts")
    relationship_rows = _relationship_from_clause(relationships)
    rel = (
        sa.select(*relationship_rows.c)
        .where(
            relationship_rows.c.domain_concept_id_1 == spec.from_domain_concept_id,
            relationship_rows.c.domain_concept_id_2 == spec.to_domain_concept_id,
            relationship_rows.c.relationship_concept_id == spec.relationship_concept_id,
        )
        .subquery("canonical_fact_relationships")
    )

    from_join = sa.and_(
        rel.c.domain_concept_id_1 == from_rows.c[str(FactColumn.domain_concept_id)],
        rel.c.fact_id_1 == from_rows.c[str(ClinicalEventColumn.event_id)],
    )
    to_join = sa.and_(
        rel.c.domain_concept_id_2 == to_rows.c[str(FactColumn.domain_concept_id)],
        rel.c.fact_id_2 == to_rows.c[str(ClinicalEventColumn.event_id)],
    )
    joined = rel.outerjoin(from_rows, from_join).outerjoin(to_rows, to_join)
    from_present = from_rows.c[str(ClinicalEventColumn.event_id)].is_not(None)
    to_present = to_rows.c[str(ClinicalEventColumn.event_id)].is_not(None)
    both_present = sa.and_(from_present, to_present)
    same_person = (
        from_rows.c[str(ClinicalEventColumn.person_id)]
        == to_rows.c[str(ClinicalEventColumn.person_id)]
    )

    matches = (
        sa.select(
            rel.c.relationship_concept_id.label(
                str(FactRelationshipColumn.relationship_concept_id)
            ),
            *_endpoint_columns(from_rows, side="from"),
            *_endpoint_columns(to_rows, side="to"),
        )
        .select_from(joined)
        .where(both_present, same_person)
    )
    if not diagnostics:
        return FactRelationshipQueries(matches=matches)

    branch_specs: list[
        tuple[FactRelationshipDiagnosticCode, sa.ColumnElement[bool], str]
    ] = []
    if from_source.complete:
        branch_specs.append(
            (
                FactRelationshipDiagnosticCode.missing_from_fact,
                sa.not_(from_present),
                "the relationship's from-fact does not exist",
            )
        )
    if to_source.complete:
        branch_specs.append(
            (
                FactRelationshipDiagnosticCode.missing_to_fact,
                sa.not_(to_present),
                "the relationship's to-fact does not exist",
            )
        )
    branch_specs.append(
        (
            FactRelationshipDiagnosticCode.person_mismatch,
            sa.and_(both_present, sa.not_(same_person)),
            "the related facts belong to different people",
        )
    )
    branches: list[sa.Select[Any]] = []
    for code, condition, message in branch_specs:
        diagnostic_code, diagnostic_message = _diagnostic_literal_columns(
            str(code),
            code_label=str(FactRelationshipDiagnosticColumn.diagnostic_code),
            message=message,
            message_label=str(FactRelationshipDiagnosticColumn.message),
        )
        branches.append(
            sa.select(
                diagnostic_code,
                rel.c.relationship_concept_id.label(
                    str(FactRelationshipDiagnosticColumn.relationship_concept_id)
                ),
                rel.c.domain_concept_id_1.label(
                    str(FactRelationshipDiagnosticColumn.from_domain_concept_id)
                ),
                rel.c.fact_id_1.label(
                    str(FactRelationshipDiagnosticColumn.from_fact_id)
                ),
                rel.c.domain_concept_id_2.label(
                    str(FactRelationshipDiagnosticColumn.to_domain_concept_id)
                ),
                rel.c.fact_id_2.label(str(FactRelationshipDiagnosticColumn.to_fact_id)),
                diagnostic_message,
            )
            .select_from(joined)
            .where(condition)
        )
    return FactRelationshipQueries(
        matches=matches,
        diagnostics=sa.union_all(*branches),
    )
