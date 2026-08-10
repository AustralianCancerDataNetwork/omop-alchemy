"""Retrieve and summarise the clinical facts an episode contains.

Once an episode exists, the recurring question is what belongs to it.
Some facts are linked explicitly through ``Episode_Event``; others fall
inside the episode's dates but were never linked.  This module resolves
both, and is explicit about which rule admitted each row.

Explicit ``Episode_Event`` links are always honoured.  A bounded
date-based fallback can be enabled per caller for sources that do not
populate episode links reliably, and ``episode_attachment_window`` defines
the window used.  Open-ended episodes are handled without letting the
window run unbounded.

``DrugEpisodeMixin`` adds linked-drug retrieval to any episode view.
Subclasses supply the concept filter and grouping key::

    class MyEpisode(DrugEpisodeMixin, EpisodeView):
        _drug_concept_ids = my_concept_ids

    episode.drug_exposures                  # resolved rows
    episode.drug_exposure_summaries_by()    # grouped summaries

Summaries group exposures by any key the caller chooses — drug concept,
ingredient, or regimen member — via ``DrugExposureSummary`` and
``summarize_drug_exposures_by``.

Dose quantities are frequently not comparable across agents, because
source units and quantities arrive unnormalised.  ``DoseEvaluability``
carries that judgement alongside the number, so a summary that cannot be
interpreted as a dose says so rather than presenting a misleading total.

An explicit ``Episode_Event`` link can still fail to resolve — a miscoded
field concept, a target class not yet registered, a dangling reference.
``ResolvedEpisodeEvent`` reports which of those applied instead of
silently returning ``None``; mix ``ResolvedEpisodeEventMixin`` into an
episode view to reach it through ordinary ``episode.episode_events``
traversal.
"""

from .dosing import DoseEvaluability
from .drug_episode import DrugEpisodeMixin
from .event_windowing import (
    DEFAULT_EPISODE_OPEN_END_FALLBACK_DAYS,
    DEFAULT_EPISODE_WINDOW_DAYS_PRIOR,
    episode_attachment_window,
)
from .exposure_series import resolve_drug_exposure_series
from .resolved_event import (
    EpisodeEventResolutionDiagnostic,
    ResolutionDiagnosticKind,
    ResolvedEpisodeEvent,
    ResolvedEpisodeEventMixin,
)
from .summaries import (
    DrugExposureSummary,
    summarize_drug_exposures,
    summarize_drug_exposures_by,
)

__all__ = [
    "DEFAULT_EPISODE_OPEN_END_FALLBACK_DAYS",
    "DEFAULT_EPISODE_WINDOW_DAYS_PRIOR",
    "DoseEvaluability",
    "DrugEpisodeMixin",
    "DrugExposureSummary",
    "EpisodeEventResolutionDiagnostic",
    "ResolutionDiagnosticKind",
    "ResolvedEpisodeEvent",
    "ResolvedEpisodeEventMixin",
    "episode_attachment_window",
    "resolve_drug_exposure_series",
    "summarize_drug_exposures",
    "summarize_drug_exposures_by",
]
