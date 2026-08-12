
from omop_alchemy.cdm.model.clinical import Measurement, Person, Condition_Occurrence, Drug_Exposure
from sqlalchemy.orm import object_session
from sqlalchemy import select
from datetime import datetime, time, date
from typing import Optional, Mapping, Any,  List
import json
from dataclasses import dataclass
from typing import Protocol, Union, Literal


TemporalKind = Literal["point", "interval"]

EventValueType = Literal["numeric", "concept", "string", "none"]

@dataclass(frozen=True)
class EventValue:
    type: EventValueType
    value: Optional[Union[float, int, str]]

    @property
    def is_numeric(self) -> bool:
        return self.type == "numeric"

    @property
    def is_concept(self) -> bool:
        return self.type == "concept"


@dataclass(frozen=True)
class EventTime:
    """
    Canonical temporal representation for an event.
    """
    start: datetime
    end: Optional[datetime] = None

    @property
    def kind(self) -> TemporalKind:
        return "interval" if self.end is not None else "point"
    

class ClinicalEventProtocol(Protocol):
    """
    Interface for ORM rows that can be projected into a patient timeline.
    """

    @property
    def person_id(self) -> int: ...

    """Primary clinical concept driving the event"""
    @property
    def concept_id(self) -> int: ...

    """
    Canonical event time.
    Handles date vs datetime, start-only vs start+end.
    """
    @property
    def event_time(self) -> EventTime:...

    """
    Numeric or categorical values associated with the event.
    Used for feature construction (e.g. value_as_number).
    """
    def event_value(self) -> EventValue: ...


    """
    Non-feature metadata (units, source value, modifiers).
    """
    def event_metadata(self) -> Mapping[str, Any]: ...

    def to_dict(self) -> dict[str, Any]: ...
    def to_json(self) -> str: ...


@dataclass
class EventMapping:
    concept_field: str
    start_date_field: str
    start_datetime_field: Optional[str] = None
    end_date_field: Optional[str] = None
    end_datetime_field: Optional[str] = None
    value_fields: Optional[List[str]] = None

def _as_datetime(d: date | datetime | None, *, end: bool = False) -> datetime | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d
    return datetime.combine(d, time.max if end else time.min)

class ClinicalEvent:
    
    _mapping: EventMapping

    @property
    def concept_id(self) -> int:
        return getattr(self, self._mapping.concept_field)
    
    def event_value(self) -> EventValue:

        fields = self._mapping.value_fields or []

        for field in fields:
            value = getattr(self, field, None)
            if value is None:
                continue

            if 'concept' in field.lower() and 'number' not in field.lower() and isinstance(value, int) and value != 0:
                return EventValue(type="concept", value=value)
            
            if 'number' in field.lower() and isinstance(value, (int, float)):
                return EventValue(type="numeric", value=value)

            if 'string' in field.lower() and isinstance(value, str) and value.strip():
                return EventValue(type="string", value=value)

        return EventValue(type="none", value=None)
    
    @property
    def event_time(self) -> EventTime:
        m = self._mapping
        start = None
        if m.start_datetime_field:
            start = getattr(self, m.start_datetime_field)
        if start is None:
            start = _as_datetime(getattr(self, m.start_date_field), end=False)
        if start is None:
            raise ValueError(f"{self.__class__.__name__}: could not resolve event start time")
        end = None
        if m.end_datetime_field:
            end = getattr(self, m.end_datetime_field)
        if end is None and m.end_date_field:
            end = _as_datetime(getattr(self, m.end_date_field), end=True)
        if end is not None and end <= start:
            end = None
        return EventTime(start=start, end=end)
    
    def event_metadata(self) -> Mapping[str, Any]:
        return {}
    

    def __repr__(self: ClinicalEventProtocol) -> str:  # ty: ignore[invalid-method-override]
        et = self.event_time
        ev = self.event_value()

        # time string
        if et.end is None:
            time_str = et.start.isoformat()
        else:
            time_str = f"{et.start.isoformat()} → {et.end.isoformat()}"

        # value string
        if ev.type == "numeric":
            value_str = f"{ev.value}"
        elif ev.type == "concept":
            value_str = f"concept:{ev.value}"
        elif ev.type == "string":
            value_str = f"'{ev.value}'"
        else:
            value_str = "∅"

        return (
            f"<{self.__class__.__name__} "
            f"person={self.person_id} "
            f"concept={self.concept_id} "
            f"time={time_str} "
            f"value={value_str}>"
        )
    
    def to_dict(self: ClinicalEventProtocol) -> dict[str, Any]:
        et = self.event_time
        ev = self.event_value()

        return {
            "person_id": self.person_id,
            "concept_id": self.concept_id,
            "event_start": et.start.isoformat(),
            "event_end": et.end.isoformat() if et.end else None,
            "value": {
                "type": ev.type,
                "value": ev.value,
            },
            "metadata": dict(self.event_metadata() or {}),
        }

    def to_json(self: ClinicalEventProtocol) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)



class Condition_Event(Condition_Occurrence, ClinicalEvent):
    _mapping = EventMapping(
        concept_field="condition_concept_id",
        start_date_field="condition_start_date",
        start_datetime_field="condition_start_datetime",
        end_date_field="condition_end_date",
        end_datetime_field="condition_end_datetime",
    )


class Measurement_Event(ClinicalEvent, Measurement):

    _mapping = EventMapping(
        concept_field="measurement_concept_id",
        start_date_field="measurement_date",
        start_datetime_field="measurement_datetime",
        value_fields=[
            "value_as_concept_id",
            "value_as_number",
            "value_as_string",
        ],
    )

    def event_metadata(self) -> dict[str, Optional[int]]:
        metadata = {
            "unit_concept_id": self.unit_concept_id
        }
        return metadata


class Drug_Exposure_Event(Drug_Exposure, ClinicalEvent):

    _mapping = EventMapping(
        concept_field="drug_concept_id",
        start_date_field="drug_exposure_start_date",
        start_datetime_field="drug_exposure_start_datetime",
        end_date_field="drug_exposure_end_date",
        end_datetime_field="drug_exposure_end_datetime",
        value_fields=["quantity"],
    )
    
    def event_metadata(self) -> Mapping[str, Any]:
        metadata = {
            "route_source_value": self.route_source_value,
            "dose_unit_source_value": self.dose_unit_source_value,
        }
        return metadata


class Person_Timeline(Person):

    EVENT_TABLES = (Measurement_Event, Condition_Event, Drug_Exposure_Event)


    @property
    def events(self) -> list[ClinicalEvent]:
        session = object_session(self)
        if session is None:
            return []

        events: list[ClinicalEvent] = []

        for EventCls in self.EVENT_TABLES:
            stmt = (
                select(EventCls)
                .where(EventCls.person_id == self.person_id)
            )
            events.extend(session.execute(stmt).scalars())

        return events
    
    @property
    def timeline(self) -> list[ClinicalEvent]:
        return sorted(
            self.events,
            key=lambda e: e.event_time.start,
        )
    
    def to_json(self) -> list[str]:  # ty: ignore[invalid-method-override]
        return [e.to_json() for e in self.timeline]  # ty: ignore[invalid-argument-type]