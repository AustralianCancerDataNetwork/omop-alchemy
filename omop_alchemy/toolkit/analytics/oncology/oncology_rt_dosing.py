from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from functools import cached_property
from typing import Any, Callable, Hashable, Sequence

from omop_alchemy.toolkit.episodes.handling import DoseEvaluability

from .oncology_procedure_occurrence import OncologyProcedure


@dataclass(frozen=True)
class RTDoseSummary:
    """
    Radiotherapy procedure summary for one caller-chosen site/group key.

    OMOP procedure rows do not provide one universal RT dose model. This summary
    exposes dates, procedure concepts, modifiers, counts, and evaluability so a
    site-specific RT policy can decide what is clinically meaningful.
    """

    group_key: object
    n_procedures: int
    first_date: date | None
    last_date: date | None
    total_quantity: int | None
    procedure_concept_ids: frozenset[int]
    modifier_concept_ids: frozenset[int]
    evaluability: DoseEvaluability


def rt_dose_evaluability(
    procedures: Sequence[OncologyProcedure],
) -> DoseEvaluability:
    if not procedures:
        return DoseEvaluability(False, "no_rt_procedures")
    if any(procedure.quantity is None for procedure in procedures):
        return DoseEvaluability(False, "missing_quantity")
    return DoseEvaluability(True)


def summarize_rt_procedures(
    procedures: Sequence[OncologyProcedure],
    *,
    group_key: object,
) -> RTDoseSummary:
    dates = [
        procedure.procedure_date
        for procedure in procedures
        if procedure.procedure_date is not None
    ]
    quantities = [
        procedure.quantity
        for procedure in procedures
        if procedure.quantity is not None
    ]
    return RTDoseSummary(
        group_key=group_key,
        n_procedures=len(procedures),
        first_date=min(dates) if dates else None,
        last_date=max(dates) if dates else None,
        total_quantity=sum(quantities) if quantities else None,
        procedure_concept_ids=frozenset(
            procedure.procedure_concept_id
            for procedure in procedures
        ),
        modifier_concept_ids=frozenset(
            procedure.modifier_concept_id
            for procedure in procedures
            if procedure.modifier_concept_id is not None
        ),
        evaluability=rt_dose_evaluability(procedures),
    )


def summarize_rt_procedures_by(
    procedures: Sequence[OncologyProcedure],
    key: Callable[[OncologyProcedure], Hashable],
) -> list[RTDoseSummary]:
    grouped: dict[Hashable, list[OncologyProcedure]] = defaultdict(list)
    for procedure in procedures:
        grouped[key(procedure)].append(procedure)
    return [
        summarize_rt_procedures(rows, group_key=group_key)
        for group_key, rows in grouped.items()
    ]


def rt_site_key(procedure: OncologyProcedure) -> object:
    if procedure.modifier_concept_id is not None:
        return procedure.modifier_concept_id
    if procedure.procedure_source_value is not None:
        return procedure.procedure_source_value
    return "unknown_site"


class OncologyRTDosingMixin:
    """
    RT dose/site summary interface for oncology treatment episodes.
    """

    @property
    def _linked_oncology_events(self) -> list[Any]:
        raise NotImplementedError

    @cached_property
    def rt_procedures(self) -> list[OncologyProcedure]:
        procedures = [
            event
            for event in self._linked_oncology_events
            if isinstance(event, OncologyProcedure) and event.is_radiotherapy
        ]
        procedures.sort(key=lambda procedure: procedure.procedure_date)
        return procedures

    @cached_property
    def rt_dose_summaries_by_site(self) -> list[RTDoseSummary]:
        return summarize_rt_procedures_by(self.rt_procedures, rt_site_key)

    @cached_property
    def rt_dose_summary(self) -> RTDoseSummary:
        return summarize_rt_procedures(
            self.rt_procedures,
            group_key="all_rt",
        )
