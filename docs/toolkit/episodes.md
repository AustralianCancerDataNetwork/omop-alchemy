# episodes

Domain-neutral machinery for building episodes and retrieving what belongs to them. A
drug episode behaves the same whether the drug is a cytotoxic agent or an antibiotic, so
everything here takes concept filters and grouping keys as parameters rather than
assuming a clinical specialty. Domain-specific episode classes — for example
`OncologyEpisode` — compose these pieces with their own concept sets and live in
[`analytics`](analytics.md).

## derivation

How episodes are constructed and related to one another — building episode queries and
resolving parent/child hierarchy, written against the raw `Episode`/`Episode_Event`
tables rather than any materialised view.

Not yet populated. The equivalent built against materialised-view subclasses lives in
`omop-constructs`.

## handling

What is inside an episode once it exists.

**Linked drug exposures.** `DrugEpisodeMixin` adds retrieval and grouped summaries to
any episode view:

```python
from omop_alchemy.toolkit.episodes.handling import DrugEpisodeMixin

class MyEpisode(DrugEpisodeMixin, EpisodeView):
    _drug_concept_ids = my_concept_ids

episode.drug_exposures                # resolved Drug_Exposure rows
episode.drug_exposure_summaries_by()  # grouped by drug concept by default
```

Construct a summary for an already selected set of rows through the summary
type itself:

```python
from omop_alchemy.toolkit.episodes.handling import DrugExposureSummary

summary = DrugExposureSummary.from_exposures(exposures, group_key="regimen-a")
```

Dose quantities are frequently not comparable across agents, because source units and
quantities arrive unnormalised. `DoseEvaluability` carries that judgement alongside the
number, so a summary that cannot be interpreted as a dose says so rather than presenting
a misleading total.

**Explicit links versus admitted-by-window.** Facts linked through `Episode_Event` are
always honoured. `episode_attachment_window` computes the bounded, date-based fallback
window used when a caller opts in to admitting same-person facts that weren't explicitly
linked.

**Resolution diagnostics.** `Episode_EventView.resolved_event` already resolves an
`Episode_Event` link best-effort, returning `None` on failure. `ResolvedEpisodeEvent`
extends it to explain *why* — a miscoded field concept, a target class not yet
registered, or a genuinely dangling reference:

```python
from omop_alchemy.toolkit.episodes.handling import ResolvedEpisodeEvent

ee = session.get(ResolvedEpisodeEvent, (episode_id, event_id, field_concept_id))
ee.resolved_event               # the resolved row, or None
ee.event_resolution_diagnostics # [] if resolved cleanly, otherwise why not
```

Mix `ResolvedEpisodeEventMixin` into an episode view to reach diagnostics through
ordinary `episode.episode_events` traversal instead of a direct query — this is how
`OncologyEpisode` gets diagnostics on oncology-aware event resolution for free.

::: omop_alchemy.toolkit.episodes.handling
