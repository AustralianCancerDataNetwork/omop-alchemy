from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import object_session

from omop_alchemy.cdm.model import Drug_Exposure


def _episode_date_bounds(episode):
    start = episode.episode_start_date
    end = episode.episode_end_date or start
    return start, end


def resolve_drug_exposure_series(
    episode,
    concept_ids: Optional[Sequence[int]] = None,
    *,
    include_window: bool = False,
) -> list[Drug_Exposure]:
    """
    Resolve drug exposures linked to an episode.

    Explicit ``Episode_Event`` links are always honoured. A bounded episode-date
    fallback can be enabled by callers that have a meaningful concept set and
    want to recover unlinked rows. The default stays explicit-only to avoid
    accidentally attributing unrelated same-person medications to a drug
    episode.
    """
    concept_id_set = set(concept_ids) if concept_ids is not None else None
    seen_ids: set[int] = set()
    exposures: list[Drug_Exposure] = []

    for event in episode.events:
        if not isinstance(event, Drug_Exposure):
            continue
        if concept_id_set is not None and event.drug_concept_id not in concept_id_set:
            continue
        exposures.append(event)
        seen_ids.add(event.drug_exposure_id)

    session = object_session(episode)
    if include_window and session is not None:
        start, end = _episode_date_bounds(episode)
        stmt = select(Drug_Exposure).where(
            Drug_Exposure.person_id == episode.person_id,
            Drug_Exposure.drug_exposure_start_date.between(start, end),
        )
        if concept_id_set is not None:
            stmt = stmt.where(Drug_Exposure.drug_concept_id.in_(concept_id_set))

        for row in session.execute(stmt).scalars():
            if row.drug_exposure_id in seen_ids:
                continue
            exposures.append(row)
            seen_ids.add(row.drug_exposure_id)

    exposures.sort(key=lambda e: e.drug_exposure_start_date)
    return exposures
