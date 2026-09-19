"""Canonical projections for Domain-scoped OMOP facts."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.sql.selectable import FromClause

from omop_alchemy.toolkit._utils import _as_from_clause, _require_columns
from omop_alchemy.toolkit.core.events import (
    ClinicalEventColumn,
    canonical_event_projection,
)

from .contracts import FactColumn
from .metadata import FactSource, fact_domain_spec


class InvalidFactSourceError(ValueError):
    """Raised when a supplied fact projection lacks canonical columns."""


def canonical_fact_projection(model: type[Any]) -> sa.Select[Any]:
    """Extend the existing canonical event projection with Domain identity."""
    domain = fact_domain_spec(model)
    events = canonical_event_projection(model, include_values=False).subquery(
        "clinical_facts"
    )
    return sa.select(
        sa.literal(domain.domain_concept_id).label(str(FactColumn.domain_concept_id)),
        *events.c,
    )


def _fact_from_clause(source: FactSource, *, name: str) -> FromClause:
    if isinstance(source.source, type):
        facts = canonical_fact_projection(source.source).subquery(name)
    else:
        facts = _as_from_clause(source.source, name=name).alias(name)
    _require_columns(
        facts.c.keys(),
        (
            str(FactColumn.domain_concept_id),
            *(str(column) for column in ClinicalEventColumn.required_columns()),
        ),
        role=name.replace("_", " "),
        error_type=InvalidFactSourceError,
    )
    return facts
