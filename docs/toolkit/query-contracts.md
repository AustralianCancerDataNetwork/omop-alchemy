# Query contracts

Consider a procedure recorded on the same day as two overlapping treatment episodes. The procedure may already have a valid `Episode_Event` link, or it may need to be assigned from dates alone. A reliable query has to answer several questions explicitly: what identifies the procedure, whether an explicit link takes precedence, whether fallback may attach it to one or both episodes, and how equally plausible candidates are ordered.

The contracts on this page provide a common vocabulary for those decisions. They are small, immutable values that can be shared by query-building code, configuration, and tests. Projection and predicate helpers translate the contracts into SQLAlchemy statements without opening a database connection.

!!! note "Construction and execution"
    Creating a contract, statement, CTE, or predicate is side-effect free. Database access begins only when the resulting statement is executed through a connection or session. The toolkit supplies projection, attachment, hierarchy, temporal, concept-set, mapping, and observation builders.

## Start with event identity

OMOP primary keys are scoped to their source tables. These three rows represent three different events even though they all use event ID 7:

| Source table | Event ID | Person | Date |
|---|---:|---:|---|
| Measurement | 7 | 101 | 20 January 2026 |
| Procedure Occurrence | 7 | 101 | 20 January 2026 |
| Observation | 7 | 202 | 20 January 2026 |

`ClinicalEventIdentity` keeps the table and numeric ID together:

```python
from omop_alchemy.toolkit.core.events import ClinicalEventIdentity

measurement = ClinicalEventIdentity("measurement", 7)
procedure = ClinicalEventIdentity("procedure_occurrence", 7)
observation = ClinicalEventIdentity("observation", 7)

assert len({measurement, procedure, observation}) == 3
```

A cross-table projection needs more than an identity. `CANONICAL_EVENT_REQUIRED_COLUMNS` defines the labels a consumer can rely on:

| Column | Meaning |
|---|---|
| `person_id` | Person who owns the source event |
| `event_id` | Primary key in the source table |
| `event_source_table` | Table that scopes `event_id` |
| `event_field_concept_id` | OMOP Field concept naming the source table's ID column |
| `event_date` | Date used for temporal selection |
| `event_datetime` | Source datetime when one is available |
| `event_concept_id` | Primary clinical concept carried by the event |

Numeric value, value concept, and unit labels are available through `CANONICAL_EVENT_OPTIONAL_COLUMNS` when a source table supports them.

The Field concept is not interchangeable with the event's clinical concept. For example, a Procedure Occurrence projection uses the Field concept for `procedure_occurrence.procedure_occurrence_id` as its discriminator and the row's `procedure_concept_id` as its clinical concept.

Build one shared event stream by passing the source models to `canonical_event_union()`:

```python
from omop_alchemy.cdm.model import Measurement, Observation, Procedure_Occurrence
from omop_alchemy.toolkit.core.events import canonical_event_union

events = canonical_event_union(
    Measurement,
    Observation,
    Procedure_Occurrence,
).subquery("clinical_events")
```

All branches expose the same labels. The source table and Field concept are literals derived from model metadata, so they remain available after the tables are combined.

## Attach an event to an episode

A complete attachment key adds the episode ID to the table-scoped event identity:

```python
from omop_alchemy.toolkit.episodes.derivation import EpisodeAttachmentIdentity

attachment = EpisodeAttachmentIdentity.from_event(
    procedure,
    episode_id=1002,
)

assert attachment.event == procedure
```

Before accepting an explicit link, the query must confirm that the Field concept names the event's actual source table and that the event and episode belong to the same person. A source mismatch is a `discriminator_mismatch`; a person mismatch is a `person_mismatch`. A link to a missing source row is a `dangling_event`, but absence from an arbitrary event projection is not enough to establish that condition because the projection may intentionally be filtered. Diagnose dangling links from an unfiltered source table through the episode-event resolution APIs.

Once valid, an explicit link takes precedence under either explicit-first policy. Suppose Procedure Occurrence 7 is linked to episode 1002, while its date also falls inside the windows of episodes 1001 and 1002. The result is only `(procedure_occurrence, 7, 1002)`: fallback must not add episode 1001 or duplicate episode 1002.

`EpisodeAttachmentPolicy` controls what happens when no valid explicit link exists:

| Policy | Fallback behaviour |
|---|---|
| `explicit_only` | Leave the event unattached |
| `explicit_first_ranked` | Select one date-eligible episode using a separate ranking specification |
| `explicit_first_all_in_window` | Retain every date-eligible episode |

Choosing between ranked and all-in-window fallback is a statement about result grain. Ranked fallback produces at most one episode per event. All-in-window fallback intentionally allows one event to appear against several overlapping episodes.

`episode_attachment_queries()` applies the complete precedence rule. It accepts a canonical event statement or one supported event model, validates explicit links against `Episode_Event`, and applies fallback only to events that have no valid explicit link:

```python
from omop_alchemy.toolkit.episodes.derivation import (
    EpisodeAttachmentPolicy,
    TemporalRankingSpec,
    TemporalSelectionPolicy,
    episode_attachment_queries,
)

attachment_queries = episode_attachment_queries(
    events,
    policy=EpisodeAttachmentPolicy.explicit_first_ranked,
    ranking=TemporalRankingSpec(
        policy=TemporalSelectionPolicy.nearest,
        stable_id_column="episode_id",
    ),
    include_diagnostics=True,
)

attachments = session.execute(attachment_queries.attachments).mappings().all()
assert attachment_queries.diagnostics is not None
diagnostics = session.execute(attachment_queries.diagnostics).mappings().all()
```

The attachment result preserves the event projection and adds `episode_id` and `attachment_method`. Its uniqueness key is `(event_source_table, event_id, episode_id)`. A valid explicit link may legitimately connect an event to more than one episode; each relationship remains a separate attachment under that key.

Diagnostics are advisory rows and do not change the attachments. They identify discriminator and person mismatches, fallback ambiguity, and events for which no valid explicit link or fallback candidate exists. A discriminator mismatch is reported relative to a particular projected event: an `Episode_Event` row with event ID 7 and the Measurement Field concept is a valid link for Measurement 7 but a rejected candidate for Procedure Occurrence 7.

## Rank fallback candidates

Ranking has two independent parts: which side of the anchor date should be considered first, and how candidates on that side should be ordered. Keeping them separate supports both symmetric nearest-date matching and the common preference for an episode that had already started when the event occurred.

For an event on 20 January, consider these episode starts:

| Episode | Start date | Absolute distance | State on 20 January |
|---|---|---:|---|
| 1001 | 15 January | 5 days | Already started |
| 1003 | 21 January | 1 day | Not yet started |

A side-neutral nearest policy selects episode 1003:

```python
from omop_alchemy.toolkit.episodes.derivation import (
    TemporalRankingSpec,
    TemporalSelectionPolicy,
)

absolute_nearest = TemporalRankingSpec(
    policy=TemporalSelectionPolicy.nearest,
    stable_id_column="episode_id",
)
```

If the analysis should prefer an episode that was underway when the event happened, apply a side preference before distance:

```python
from omop_alchemy.toolkit.episodes.derivation import TemporalSidePreference

already_started_first = TemporalRankingSpec(
    policy=TemporalSelectionPolicy.nearest,
    stable_id_column="episode_id",
    side_preference=TemporalSidePreference.on_or_before_anchor,
)
```

This policy selects episode 1001. Absolute distance still orders episodes within the preferred side; it simply does not allow a closer future episode to outrank every episode that had already started. `on_or_after_anchor` expresses the corresponding future-first rule.

Apply the policy to SQLAlchemy columns with `temporal_order_expressions()`:

```python
from omop_alchemy.cdm.model.structural import Episode
from omop_alchemy.toolkit.episodes.derivation import temporal_order_expressions

ordering = temporal_order_expressions(
    Episode.episode_start_date,
    events.c.event_date,
    Episode.episode_id,
    already_started_first,
)

candidate_episodes = candidate_episodes.order_by(*ordering)
```

`earliest` and `latest` are available when chronological position, rather than distance from the anchor, defines the result. Every policy ends with the named stable ID column in ascending order. If episodes 1001 and 1002 are otherwise tied, 1001 wins consistently rather than relying on database return order.

### Date boundaries

Lower and upper bounds are inclusive by default and can be changed independently through `include_lower_bound` and `include_upper_bound`. For an episode starting 15 January 2026 with a 90-day prior window, 17 October 2025 lies exactly on the lower boundary and is included under the default. If the episode ends on 5 February, that date is included while 6 February is not.

Boundary choices belong in the ranking specification rather than being hidden in a comparison operator. This is especially important when two systems use similar-looking windows but disagree at exactly 90 or 180 days.

`episode_window_predicate()` uses the same finite defaults as the in-memory episode window. It honours a recorded episode end and substitutes a bounded post-start end only when the end is missing:

```python
from omop_alchemy.toolkit.episodes.derivation import episode_window_predicate

inside_episode_window = episode_window_predicate(
    events.c.event_date,
    Episode.episode_start_date,
    Episode.episode_end_date,
    ranking=already_started_first,
)
```

## Select one repeated observation

Repeated observations need the same explicit treatment of direction, grouping, and ties. For an anchor date of 20 January, suppose a person has these rows:

| Observation ID | Date | Value |
|---:|---|---|
| 21 | 1 January | earlier |
| 22 | 20 January | anchor-a |
| 23 | 20 January | anchor-b |
| 24 | 21 January | after-anchor |

The following specification chooses the latest observation on or before the anchor, grouping rows by person and observation concept:

```python
from omop_alchemy.toolkit.episodes.derivation import (
    ObservationSelectionPolicy,
    ObservationSelectionSpec,
)

selection = ObservationSelectionSpec(
    policy=ObservationSelectionPolicy.latest_on_or_before_anchor,
    partition_by=("person_id", "observation_concept_id"),
    stable_id_column="observation_id",
    include_anchor_date=True,
)
```

Use `ranked_observation_select()` to apply the anchor filter before calculating row numbers:

```python
from datetime import date

from sqlalchemy import literal, select

from omop_alchemy.cdm.model import Observation
from omop_alchemy.toolkit.episodes.derivation import ranked_observation_select

ranked = ranked_observation_select(
    Observation.__table__,
    selection,
    anchor_date=literal(date(2026, 1, 20)),
).subquery("ranked_observations")

selected = select(ranked).where(ranked.c.observation_rank == 1)
```

Observation 24 is after the anchor and is therefore excluded. Observations 22 and 23 tie on date, so the stable ID selects 22. That tie-break creates reproducible output; it does not claim that one same-day clinical value is more correct. If every same-day value is meaningful, retain them by choosing a result grain that includes the observation ID instead of reducing the group to one row.

Add `episode_id` or another field to `partition_by` when selection must occur separately within those groups. The partition is part of the clinical meaning of the result, not merely an optimisation detail.

## Runtime concept sets

Applications often receive concept selection as configuration rather than as a compile-time governed unit. `RuntimeConceptSetSpec` records four inputs: exact concepts and descendants to include, and exact concepts and descendants to exclude.

```python
from omop_alchemy.toolkit.core.concepts import RuntimeConceptSetSpec

concepts = RuntimeConceptSetSpec(
    include_ancestor_ids=(100,),
    include_exact_ids=(900,),
    exclude_ancestor_ids=(400,),
    exclude_exact_ids=(901,),
    require_standard=True,
    include_classification=False,
)
```

The intended set is:

```text
(descendants of 100 OR exact concept 900)
AND NOT (descendants of 400 OR exact concept 901)
```

Exclusion wins when a concept is reached from both sides. With no inclusion, the set matches nothing. IDs are sorted and deduplicated when the specification is created.

`require_standard` and `include_classification` use the same vocabulary as `ConceptFilter` and `ConceptGroupSpec`; predicate rendering delegates to the existing normalised OMOP standardness expressions. The specification does not decide whether a numeric ID is valid in a particular vocabulary. Validate configuration and local-concept policy at the boundary where those rules are known.

`runtime_concept_predicate()` translates the specification into database-side `concept_ancestor` and `concept` predicates:

```python
from sqlalchemy import select

from omop_alchemy.cdm.model import Procedure_Occurrence
from omop_alchemy.toolkit.core.concepts import runtime_concept_predicate

matching_procedures = select(Procedure_Occurrence).where(
    runtime_concept_predicate(
        Procedure_Occurrence.procedure_concept_id,
        concepts,
    )
)
```

Constructing the specification or predicate performs no hierarchy expansion and no database access. Descendants are resolved by the database when the surrounding statement is executed.

Some applications compose positive and negative rules independently rather than collecting them into one runtime set. `descendant_concept_select()` provides the lower-level hierarchy operation for that case and returns each matching descendant once:

```python
from omop_alchemy.toolkit.core.concepts import descendant_concept_select

matching_procedures = select(Procedure_Occurrence).where(
    Procedure_Occurrence.procedure_concept_id.in_(
        descendant_concept_select((100, 200))
    ),
    Procedure_Occurrence.procedure_concept_id.not_in(
        descendant_concept_select((400,))
    ),
)
```

Use `RuntimeConceptSetSpec` when the inclusions and exclusions form one configured set with exclusion precedence. Use `descendant_concept_select()` when the surrounding query or rule model owns how separate predicates are combined.

## API reference

::: omop_alchemy.toolkit.core.events

::: omop_alchemy.toolkit.episodes.derivation
