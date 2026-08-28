"""Canonical, domain-neutral clinical-event identities and row shapes.

Event tables use different native column names, but cross-table analytical
queries need one stable vocabulary. These contracts are shared by timeline
adapters in ``core`` and episode query builders in the higher ``episodes``
tier. They are declarative and perform no database work.
"""

from .contracts import (
    CANONICAL_EVENT_OPTIONAL_COLUMNS,
    CANONICAL_EVENT_REQUIRED_COLUMNS,
    ClinicalEventColumn,
    ClinicalEventIdentity,
    ClinicalEventRow,
    ValuedClinicalEventRow,
)

__all__ = [
    "CANONICAL_EVENT_OPTIONAL_COLUMNS",
    "CANONICAL_EVENT_REQUIRED_COLUMNS",
    "ClinicalEventColumn",
    "ClinicalEventIdentity",
    "ClinicalEventRow",
    "ValuedClinicalEventRow",
]
