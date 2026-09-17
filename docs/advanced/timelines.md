# Patient Timelines

OMOP Alchemy includes a lightweight timeline layer that projects OMOP CDM ORM objects into a **unified, time-ordered event stream** per patient.

It is primarily intended for feature construction and exploratory analysis — not for production query pipelines where raw SQLAlchemy queries are more appropriate.

---

## Core concepts

### `EventTime`

A canonical temporal representation. Every clinical event has a start datetime; an end datetime is optional. The `kind` property returns `"point"` or `"interval"`.

::: omop_alchemy.toolkit.core.timeline.event_timeline.EventTime

---

### `EventValue`

The value associated with a clinical event — numeric, concept, string, or none.

::: omop_alchemy.toolkit.core.timeline.event_timeline.EventValue

---

### `EventMapping`

Declares which ORM fields supply the concept, start/end datetimes, and value for a particular CDM table. `EventMapping.from_model()` derives identity, source, concept and start fields from the shared CDM metadata used by canonical SQL projections. It also infers independent interval endpoints from that metadata: Measurement and Observation alias their one date column in the target API and remain point events in the timeline. Timeline classes add value and display-specific fields. Explicit endpoint strings override inference; explicit `None` disables the corresponding inferred endpoint.

::: omop_alchemy.toolkit.core.timeline.event_timeline.EventMapping

---

## The `ClinicalEvent` mixin

`ClinicalEvent` is a mixin that adds timeline behaviour to any CDM ORM class. It implements the shared `toolkit.core.events.ClinicalEventRow` identity and projection fields, then reads `_mapping` to add `event_time`, `event_value`, `event_metadata`, `to_dict`, and `to_json`. The shared core contract keeps timeline events and SQL event projections aligned without making `core.timeline` import the higher-level episode package.

::: omop_alchemy.toolkit.core.timeline.event_timeline.ClinicalEvent

---

## Concrete event classes

Four CDM tables are pre-wired with `EventMapping`s:

| Class | CDM table | Concept field | Value fields |
|-------|-----------|---------------|--------------|
| `Condition_Event` | `condition_occurrence` | `condition_concept_id` | — |
| `Measurement_Event` | `measurement` | `measurement_concept_id` | `value_as_concept_id`, `value_as_number` |
| `Drug_Exposure_Event` | `drug_exposure` | `drug_concept_id` | `quantity` |
| `Observation_Event` | `observation` | `observation_concept_id` | `value_as_concept_id`, `value_as_number`, `value_as_string` |

::: omop_alchemy.toolkit.core.timeline.event_timeline.Condition_Event

::: omop_alchemy.toolkit.core.timeline.event_timeline.Measurement_Event

::: omop_alchemy.toolkit.core.timeline.event_timeline.Drug_Exposure_Event

::: omop_alchemy.toolkit.core.timeline.event_timeline.Observation_Event

---

## `Person_Timeline`

Extends the `Person` ORM class with `.events` and `.timeline` properties. Requires an active SQLAlchemy session (i.e. the object must have been loaded from a session, not constructed in memory).

::: omop_alchemy.toolkit.core.timeline.event_timeline.Person_Timeline

---

## Usage example

```python
from sqlalchemy.orm import Session
from omop_alchemy.toolkit.core.timeline import Person_Timeline

with Session(engine) as session:
    person = session.get(Person_Timeline, 42)
    for event in person.timeline:   # sorted by event_time.start
        print(event)
        print(event.to_dict())
```

Serialized timeline events include `event_id`, `event_source_table`, `event_field_concept_id` and `event_concept_id`. Direct `EventMapping` construction requires `event_id_field`, `event_source_table` and `event_field_concept_id`; `EventMapping.from_model()` derives these fields from model metadata.

---

## Extending to new tables

To add a supported CDM table to the timeline, subclass both `ClinicalEvent` and the target ORM class and build `_mapping` from its Core metadata:

```python
from omop_alchemy.toolkit.core.timeline.event_timeline import ClinicalEvent, EventMapping
from omop_alchemy.cdm.model.clinical import Procedure_Occurrence

class Procedure_Event(ClinicalEvent, Procedure_Occurrence):
    _mapping = EventMapping.from_model(Procedure_Occurrence)
```
