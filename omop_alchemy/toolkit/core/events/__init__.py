"""Canonical, domain-neutral clinical-event identities and row shapes.

Event tables use different native column names, but cross-table analytical
queries need one stable vocabulary. This area provides both the shared row
contracts and SQLAlchemy projections. Building a projection is side-effect free;
the database is accessed only when a caller executes the returned statement.
"""

from .contracts import (
    ClinicalEventColumn,
    ClinicalEventIdentity,
    ClinicalEventRow,
    ValuedClinicalEventRow,
)
from .projections import (
    canonical_event_projection,
    canonical_event_union,
)

__all__ = [
    "ClinicalEventColumn",
    "ClinicalEventIdentity",
    "ClinicalEventRow",
    "ValuedClinicalEventRow",
    "canonical_event_projection",
    "canonical_event_union",
]
