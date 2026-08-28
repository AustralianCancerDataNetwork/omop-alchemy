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
live in ``toolkit.core.events``. All are declarative and perform no database
work, so downstream packages can agree on semantics before changing clinical
queries.
"""

from .contracts import (
    AttachmentDiagnosticCode,
    EpisodeAttachmentDiagnostic,
    EpisodeAttachmentIdentity,
    EpisodeAttachmentPolicy,
    ObservationSelectionPolicy,
    ObservationSelectionSpec,
    TemporalRankingSpec,
    TemporalSelectionPolicy,
    TemporalSidePreference,
)

__all__ = [
    "AttachmentDiagnosticCode",
    "EpisodeAttachmentDiagnostic",
    "EpisodeAttachmentIdentity",
    "EpisodeAttachmentPolicy",
    "ObservationSelectionPolicy",
    "ObservationSelectionSpec",
    "TemporalRankingSpec",
    "TemporalSelectionPolicy",
    "TemporalSidePreference",
]
