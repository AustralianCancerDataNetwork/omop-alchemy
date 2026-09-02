"""Canonical, domain-neutral clinical-event identities and row shapes.

Event tables use different native column names, but cross-table analytical
queries need one stable vocabulary. This area provides both the shared row
contracts and SQLAlchemy projections. Building a projection is side-effect free;
the database is accessed only when a caller executes the returned statement.
"""

from .contracts import (
    CANONICAL_EVENT_OPTIONAL_COLUMNS,
    CANONICAL_EVENT_REQUIRED_COLUMNS,
    ClinicalEventColumn,
    ClinicalEventIdentity,
    ClinicalEventRow,
    ValuedClinicalEventRow,
)
from .projections import (
    ClinicalEventModelSpec,
    UnsupportedClinicalEventModelError,
    canonical_event_projection,
    canonical_event_union,
    clinical_event_model_spec,
)

__all__ = [
    "CANONICAL_EVENT_OPTIONAL_COLUMNS",
    "CANONICAL_EVENT_REQUIRED_COLUMNS",
    "ClinicalEventColumn",
    "ClinicalEventIdentity",
    "ClinicalEventModelSpec",
    "ClinicalEventRow",
    "ValuedClinicalEventRow",
    "UnsupportedClinicalEventModelError",
    "canonical_event_projection",
    "canonical_event_union",
    "clinical_event_model_spec",
]
