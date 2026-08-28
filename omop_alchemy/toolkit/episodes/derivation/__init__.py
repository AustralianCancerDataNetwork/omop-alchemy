"""Construct episodes and resolve the relationships between them.

Episodes in the CDM are rows that reference each other through
``episode_parent_id`` and reach clinical facts through ``Episode_Event``.
Turning that into something queryable — a regimen with its cycles, a
diagnosis with the treatment that followed — is this tier's job: queries
that select episodes by concept, join parent and child episodes into a
single result so a hierarchy can be read in one pass, and establish the
date windows relating one episode to another, written against the raw
``Episode``/``Episode_Event`` tables rather than any materialised view.

The public contracts in this area define episode-attachment identities and
policies used by query builders. Shared clinical-event row names and identities
live in ``toolkit.core.events``. Projection, attachment, and ranking helpers
return SQLAlchemy statements or expressions without executing them.
"""

from .attachments import (
    EpisodeAttachmentQueries,
    InvalidAttachmentSourceError,
    episode_attachment_queries,
)
from .contracts import (
    CANONICAL_ATTACHMENT_DIAGNOSTIC_COLUMNS,
    CANONICAL_EPISODE_COLUMNS,
    AttachmentDiagnosticCode,
    AttachmentDiagnosticColumn,
    EpisodeColumn,
    EpisodeAttachmentDiagnostic,
    EpisodeAttachmentIdentity,
    EpisodeAttachmentMethod,
    EpisodeAttachmentPolicy,
    ObservationSelectionPolicy,
    ObservationSelectionSpec,
    TemporalRankingSpec,
    TemporalSelectionPolicy,
    TemporalSidePreference,
)
from .observations import (
    observation_eligibility_predicate,
    observation_order_expressions,
    observation_row_number,
    ranked_observation_select,
)
from .structure import (
    canonical_episode_projection,
    direct_episode_relationship_projection,
    episode_descendants,
    episode_event_hierarchy_projection,
)
from .temporal import (
    absolute_day_delta,
    bounded_temporal_predicate,
    episode_window_bounds,
    episode_window_predicate,
    shift_date,
    signed_day_delta,
    temporal_order_expressions,
    temporal_row_number,
)

__all__ = [
    "AttachmentDiagnosticCode",
    "AttachmentDiagnosticColumn",
    "CANONICAL_ATTACHMENT_DIAGNOSTIC_COLUMNS",
    "CANONICAL_EPISODE_COLUMNS",
    "EpisodeColumn",
    "EpisodeAttachmentDiagnostic",
    "EpisodeAttachmentIdentity",
    "EpisodeAttachmentMethod",
    "EpisodeAttachmentPolicy",
    "EpisodeAttachmentQueries",
    "InvalidAttachmentSourceError",
    "ObservationSelectionPolicy",
    "ObservationSelectionSpec",
    "TemporalRankingSpec",
    "TemporalSelectionPolicy",
    "TemporalSidePreference",
    "absolute_day_delta",
    "bounded_temporal_predicate",
    "canonical_episode_projection",
    "direct_episode_relationship_projection",
    "episode_attachment_queries",
    "episode_descendants",
    "episode_event_hierarchy_projection",
    "episode_window_bounds",
    "episode_window_predicate",
    "observation_eligibility_predicate",
    "observation_order_expressions",
    "observation_row_number",
    "ranked_observation_select",
    "shift_date",
    "signed_day_delta",
    "temporal_order_expressions",
    "temporal_row_number",
]
