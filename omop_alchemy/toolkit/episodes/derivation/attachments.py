"""Explicit-first event-to-episode attachment queries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.sql.selectable import FromClause, SelectBase

from omop_alchemy.cdm.model.structural import Episode, Episode_Event
from omop_alchemy.toolkit.core.events import (
    CANONICAL_EVENT_REQUIRED_COLUMNS,
    ClinicalEventColumn,
    canonical_event_projection,
)
from .contracts import (
    CANONICAL_ATTACHMENT_DIAGNOSTIC_COLUMNS,
    AttachmentDiagnosticCode,
    AttachmentDiagnosticColumn,
    EpisodeAttachmentMethod,
    EpisodeAttachmentPolicy,
    EpisodeColumn,
    EpisodeWindowSpec,
    TemporalRankingSpec,
)
from .structure import canonical_episode_projection
from .temporal import episode_window_predicate, temporal_row_number


ATTACHMENT_EPISODE_ID = "episode_id"
ATTACHMENT_METHOD = "attachment_method"
_FALLBACK_RANK = "_fallback_rank"
_FALLBACK_CANDIDATE_COUNT = "_fallback_candidate_count"
_IDENTITY_RANK = "_attachment_identity_rank"


class InvalidAttachmentSourceError(ValueError):
    """Raised when an attachment input does not expose its required columns."""


@dataclass(frozen=True, slots=True)
class EpisodeAttachmentQueries:
    """Attachment results and, when requested, their advisory diagnostics.

    ``attachments`` preserves the input event columns and appends ``episode_id``
    and ``attachment_method``. Its executable uniqueness key is
    ``(event_source_table, event_id, episode_id)``.

    ``diagnostics`` is ``None`` unless requested. Diagnostics explain rejected
    explicit links and fallback outcomes; they do not alter attachment rows.
    """

    attachments: sa.Select[Any]
    diagnostics: sa.Select[Any] | None = None


def _as_from_clause(
    source: FromClause | SelectBase,
    *,
    name: str,
) -> FromClause:
    if isinstance(source, SelectBase):
        return source.subquery(name)
    if isinstance(source, FromClause):
        return source
    raise TypeError(f"{name} must be a SQLAlchemy Select or FromClause")


def _event_source(source: type[Any] | FromClause | SelectBase) -> FromClause:
    if isinstance(source, type):
        return canonical_event_projection(source).subquery("attachment_events")
    return _as_from_clause(source, name="attachment_events")


def _episode_source(
    source: type[Episode] | FromClause | SelectBase,
) -> FromClause:
    if isinstance(source, type):
        model = cast(type[Episode], source)
        return canonical_episode_projection(model).subquery("attachment_episodes")
    return _as_from_clause(source, name="attachment_episodes")


def _episode_event_source(
    source: type[Episode_Event] | FromClause | SelectBase,
) -> FromClause:
    if isinstance(source, type):
        model = cast(type[Episode_Event], source)
        return model.__table__
    return _as_from_clause(source, name="attachment_episode_events")


def _require_columns(
    source: FromClause,
    required: tuple[str, ...],
    *,
    role: str,
) -> None:
    missing = tuple(name for name in required if name not in source.c)
    if missing:
        raise InvalidAttachmentSourceError(
            f"{role} is missing required columns: {', '.join(missing)}"
        )


def _same_event(left: FromClause, right: FromClause) -> sa.ColumnElement[bool]:
    return sa.and_(
        left.c[str(ClinicalEventColumn.event_source_table)]
        == right.c[str(ClinicalEventColumn.event_source_table)],
        left.c[str(ClinicalEventColumn.event_id)]
        == right.c[str(ClinicalEventColumn.event_id)],
    )


def _not_exists_for_event(
    source: FromClause,
    keys: FromClause,
) -> sa.ColumnElement[bool]:
    return sa.not_(
        sa.exists(sa.select(1).select_from(keys).where(_same_event(source, keys)))
    )


def _attachment_diagnostics(
    events: FromClause,
    episodes: FromClause,
    episode_events: FromClause,
    valid_explicit: FromClause,
    valid_explicit_event_keys: FromClause,
    fallback_candidates: FromClause | None,
    *,
    policy: EpisodeAttachmentPolicy,
) -> sa.Select[Any]:
    event_id = str(ClinicalEventColumn.event_id)
    source_table = str(ClinicalEventColumn.event_source_table)
    event_field = str(ClinicalEventColumn.event_field_concept_id)
    episode_id = str(EpisodeColumn.episode_id)
    person_id = str(ClinicalEventColumn.person_id)
    link_field = "episode_event_field_concept_id"

    def diagnostic_literals(
        code: AttachmentDiagnosticCode,
        *,
        linked_field: sa.ColumnElement[Any],
        linked_episode_id: sa.ColumnElement[Any],
        candidate_count: sa.ColumnElement[Any],
        message: str,
    ) -> tuple[sa.ColumnElement[Any], ...]:
        return (
            sa.literal(str(code)).label(
                str(AttachmentDiagnosticColumn.diagnostic_code)
            ),
            events.c[source_table].label(source_table),
            events.c[event_id].label(event_id),
            events.c[event_field].label(event_field),
            linked_field.label(
                str(AttachmentDiagnosticColumn.linked_event_field_concept_id)
            ),
            linked_episode_id.label(episode_id),
            candidate_count.label(str(AttachmentDiagnosticColumn.candidate_count)),
            sa.literal(message).label(str(AttachmentDiagnosticColumn.message)),
        )

    null_integer = sa.cast(sa.null(), sa.Integer())
    # A discriminator-correct link is still rejected when it crosses people;
    # this protects downstream episode grains from attaching another person's
    # otherwise valid event row.
    person_mismatches = (
        sa.select(
            *diagnostic_literals(
                AttachmentDiagnosticCode.person_mismatch,
                linked_field=episode_events.c[link_field],
                linked_episode_id=episode_events.c[episode_id],
                candidate_count=null_integer,
                message="explicit link connects an event and episode belonging to different people",
            )
        )
        .select_from(
            events.join(
                episode_events,
                sa.and_(
                    events.c[event_id] == episode_events.c[event_id],
                    events.c[event_field] == episode_events.c[link_field],
                ),
            ).join(
                episodes,
                episodes.c[episode_id] == episode_events.c[episode_id],
            )
        )
        .where(events.c[person_id] != episodes.c[person_id])
    )

    diagnostic_branches: list[sa.Select[Any]] = [person_mismatches]

    if fallback_candidates is not None:
        # Candidate counts are calculated before ranked fallback reduces the
        # result. A selected row can therefore still explain that another
        # eligible episode existed and was resolved by policy.
        candidate_keys = (
            sa.select(
                fallback_candidates.c[source_table],
                fallback_candidates.c[event_id],
            )
            .distinct()
            .cte("fallback_candidate_event_keys")
        )
        ambiguous = (
            sa.select(
                *diagnostic_literals(
                    AttachmentDiagnosticCode.ambiguous_fallback,
                    linked_field=null_integer,
                    linked_episode_id=null_integer,
                    candidate_count=fallback_candidates.c[_FALLBACK_CANDIDATE_COUNT],
                    message="more than one episode is eligible for fallback attachment",
                )
            )
            .select_from(
                events.join(
                    fallback_candidates, _same_event(events, fallback_candidates)
                )
            )
            .where(fallback_candidates.c[_FALLBACK_CANDIDATE_COUNT] > 1)
            .distinct()
        )
        if not policy.permits_fallback_fanout:
            diagnostic_branches.append(ambiguous)
    else:
        candidate_keys = None

    # The final diagnostic is event-relative: no valid explicit relationship
    # and, where fallback is enabled, no episode admitted by the window.
    no_candidate_conditions = [_not_exists_for_event(events, valid_explicit_event_keys)]
    if candidate_keys is not None:
        no_candidate_conditions.append(_not_exists_for_event(events, candidate_keys))
    no_candidate_message = (
        "event has no valid explicit link"
        if policy is EpisodeAttachmentPolicy.explicit_only
        else "event has no valid explicit link or fallback episode in the configured window"
    )
    no_candidate = sa.select(
        *diagnostic_literals(
            AttachmentDiagnosticCode.no_candidate_episode,
            linked_field=null_integer,
            linked_episode_id=null_integer,
            candidate_count=sa.literal(0),
            message=no_candidate_message,
        )
    ).where(*no_candidate_conditions)
    diagnostic_branches.append(no_candidate)

    combined = sa.union_all(*diagnostic_branches).subquery("attachment_diagnostics")
    return sa.select(
        *(combined.c[str(column)] for column in CANONICAL_ATTACHMENT_DIAGNOSTIC_COLUMNS)
    ).distinct()


def episode_attachment_queries(
    events: type[Any] | FromClause | SelectBase,
    *,
    policy: EpisodeAttachmentPolicy,
    episodes: type[Episode] | FromClause | SelectBase = Episode,
    episode_events: type[Episode_Event] | FromClause | SelectBase = Episode_Event,
    ranking: TemporalRankingSpec | None = None,
    window: EpisodeWindowSpec = EpisodeWindowSpec(),
    include_diagnostics: bool = False,
) -> EpisodeAttachmentQueries:
    """Build explicit-first attachments from canonical event and episode inputs.

    Explicit links are valid only when their event ID, Field-concept
    discriminator, episode ID, and person all agree. An invalid explicit link
    never suppresses fallback. Ranked fallback requires a ranking specification;
    all-in-window fallback retains every eligible episode.
    """
    if policy.requires_fallback_ranking and ranking is None:
        raise ValueError("explicit_first_ranked requires a temporal ranking")
    if not policy.requires_fallback_ranking and ranking is not None:
        raise ValueError(f"{policy} does not use a temporal ranking")

    event_source = _event_source(events)
    episode_source = _episode_source(episodes)
    link_source = _episode_event_source(episode_events)
    event_names = tuple(column.key for column in event_source.c)

    _require_columns(
        event_source,
        tuple(str(column) for column in CANONICAL_EVENT_REQUIRED_COLUMNS),
        role="events",
    )
    _require_columns(
        episode_source,
        (
            str(EpisodeColumn.episode_id),
            str(EpisodeColumn.person_id),
            str(EpisodeColumn.episode_start_date),
            str(EpisodeColumn.episode_end_date),
        ),
        role="episodes",
    )
    _require_columns(
        link_source,
        ("episode_id", "event_id", "episode_event_field_concept_id"),
        role="episode_events",
    )
    for reserved in (ATTACHMENT_EPISODE_ID, ATTACHMENT_METHOD):
        if reserved in event_names:
            raise InvalidAttachmentSourceError(
                f"events already contains reserved attachment column {reserved!r}"
            )

    event_id = str(ClinicalEventColumn.event_id)
    event_date = str(ClinicalEventColumn.event_date)
    event_field = str(ClinicalEventColumn.event_field_concept_id)
    source_table = str(ClinicalEventColumn.event_source_table)
    person_id = str(ClinicalEventColumn.person_id)
    episode_id = str(EpisodeColumn.episode_id)
    episode_person_id = str(EpisodeColumn.person_id)
    episode_start = str(EpisodeColumn.episode_start_date)
    episode_end = str(EpisodeColumn.episode_end_date)

    # stage 1 of episode resolution accepts an explicit link only when ID, Field
    # discriminator, episode, and person agree. This is the sole point where an
    # explicit link becomes authoritative enough to suppress date-based fallback.
    valid_explicit = (
        sa.select(
            *(event_source.c[name] for name in event_names),
            episode_source.c[episode_id].label(ATTACHMENT_EPISODE_ID),
            sa.literal(str(EpisodeAttachmentMethod.explicit)).label(ATTACHMENT_METHOD),
        )
        .select_from(
            event_source.join(
                link_source,
                sa.and_(
                    event_source.c[event_id] == link_source.c[event_id],
                    event_source.c[event_field]
                    == link_source.c["episode_event_field_concept_id"],
                ),
            ).join(
                episode_source,
                sa.and_(
                    episode_source.c[episode_id] == link_source.c[episode_id],
                    episode_source.c[episode_person_id] == event_source.c[person_id],
                ),
            )
        )
        .distinct()
        .cte("valid_explicit_attachments")
    )

    # These keys distinguish events that already have authoritative links from
    # those whose absence or fallback outcome still needs explanation. Build the
    # relation once so both fallback suppression and diagnostics reference the
    # same CTE rather than constructing duplicate DISTINCT projections.
    valid_explicit_event_keys = (
        sa.select(
            valid_explicit.c[source_table],
            valid_explicit.c[event_id],
        )
        .distinct()
        .cte("valid_explicit_event_keys")
    )

    attachment_names = (*event_names, ATTACHMENT_EPISODE_ID, ATTACHMENT_METHOD)
    explicit_select = sa.select(*(valid_explicit.c[name] for name in attachment_names))
    fallback_candidates: FromClause | None = None
    attachment_branches: list[sa.Select[Any]] = [explicit_select]

    if policy.uses_fallback:
        # episode resolution stage 2 records the complete table-scoped identity
        # of every valid explicit event. The anti-existence check below must use
        # both columns: event_id alone is never a cross-table identity in OMOP.
        fallback_columns: list[sa.ColumnElement[Any]] = [
            *(event_source.c[name] for name in event_names),
            episode_source.c[episode_id].label(ATTACHMENT_EPISODE_ID),
            sa.literal(str(EpisodeAttachmentMethod.fallback)).label(ATTACHMENT_METHOD),
            sa.func.count()
            .over(partition_by=(event_source.c[source_table], event_source.c[event_id]))
            .label(_FALLBACK_CANDIDATE_COUNT),
        ]
        if policy.requires_fallback_ranking:
            assert ranking is not None  # validated above
            if ranking.stable_id_column not in episode_source.c:
                raise InvalidAttachmentSourceError(
                    "episodes is missing temporal stable ID column: "
                    f"{ranking.stable_id_column}"
                )
            # episode resolution stage 3 ranks only after window admission. A side
            # preference is a deliberate clinical policy tier; the stable episode
            # ID prevents tied dates from depending on database row order.
            fallback_columns.append(
                temporal_row_number(
                    episode_source.c[episode_start],
                    event_source.c[event_date],
                    episode_source.c[ranking.stable_id_column],
                    ranking,
                    partition_by=(
                        event_source.c[source_table],
                        event_source.c[event_id],
                    ),
                    label=_FALLBACK_RANK,
                )
            )

        # All fallback policies share the same finite episode-relative window.
        # The policy decides whether every admitted episode survives or exactly
        # one ranked candidate is retained.
        fallback_candidates = (
            sa.select(*fallback_columns)
            .select_from(
                event_source.join(
                    episode_source,
                    sa.and_(
                        event_source.c[person_id]
                        == episode_source.c[episode_person_id],
                        episode_window_predicate(
                            event_source.c[event_date],
                            episode_source.c[episode_start],
                            episode_source.c[episode_end],
                            window=window,
                        ),
                    ),
                )
            )
            .where(_not_exists_for_event(event_source, valid_explicit_event_keys))
            .cte("fallback_attachment_candidates")
        )
        selected_fallback = sa.select(
            *(fallback_candidates.c[name] for name in attachment_names)
        )
        if policy.requires_fallback_ranking:
            selected_fallback = selected_fallback.where(
                fallback_candidates.c[_FALLBACK_RANK] == 1
            )
        attachment_branches.append(selected_fallback)

    # The builder accepts arbitrary selectables as inputs, so their source keys
    # are not necessarily database constraints. Enforce the documented output
    # identity here and prefer explicit provenance if a custom input manages to
    # present the same attachment through more than one branch.
    combined = sa.union_all(*attachment_branches).cte("combined_episode_attachments")
    ranked_attachments = sa.select(
        *(combined.c[name] for name in attachment_names),
        sa.func.row_number()
        .over(
            partition_by=(
                combined.c[source_table],
                combined.c[event_id],
                combined.c[ATTACHMENT_EPISODE_ID],
            ),
            order_by=sa.case(
                (
                    combined.c[ATTACHMENT_METHOD]
                    == str(EpisodeAttachmentMethod.explicit),
                    0,
                ),
                else_=1,
            ),
        )
        .label(_IDENTITY_RANK),
    ).cte("deduplicated_episode_attachments")
    attachments = sa.select(
        *(ranked_attachments.c[name] for name in attachment_names)
    ).where(ranked_attachments.c[_IDENTITY_RANK] == 1)

    diagnostics = None
    if include_diagnostics:
        # Diagnostics explain rejected links and fallback outcomes without
        # changing attachment rows. Non-matching discriminators and missing
        # source rows are intentionally out of scope: this builder may receive
        # a filtered projection and cannot infer absence from an OMOP table.
        # ResolvedEpisodeEvent owns complete target-table resolution.
        diagnostics = _attachment_diagnostics(
            event_source,
            episode_source,
            link_source,
            valid_explicit,
            valid_explicit_event_keys,
            fallback_candidates,
            policy=policy,
        )
    return EpisodeAttachmentQueries(attachments=attachments, diagnostics=diagnostics)
