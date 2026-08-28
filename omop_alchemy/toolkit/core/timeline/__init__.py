"""Project a person's clinical records into one ordered event sequence.

Conditions, measurements, and drug exposures live in separate CDM tables
with differently named date and value columns.  Answering "what happened
to this patient, in order" means reconciling them.  ``Person_Timeline``
does that reconciliation and presents the result as a single list of
events sorted by time.

Each event implements the canonical row identity from ``toolkit.core.events``
and adds timeline-specific interval, value, metadata, and serialisation
behaviour, so callers can iterate a patient's history without special-casing
per table::

    from omop_alchemy.toolkit.core.timeline import Person_Timeline

    for event in person.timeline:
        print(event.event_time.start, event.concept_id, event.event_value().value)

The timeline is a flat chronological view.  It carries no notion of which
events group together into a treatment course or disease phase — that
grouping is what ``omop_alchemy.toolkit.episodes`` provides.
"""

from .event_timeline import (
    ClinicalEvent,
    ClinicalEventProtocol,
    Condition_Event,
    Drug_Exposure_Event,
    EventMapping,
    EventTime,
    EventValue,
    Measurement_Event,
    Person_Timeline,
)

__all__ = [
    "ClinicalEvent",
    "ClinicalEventProtocol",
    "Condition_Event",
    "Drug_Exposure_Event",
    "EventMapping",
    "EventTime",
    "EventValue",
    "Measurement_Event",
    "Person_Timeline",
]
