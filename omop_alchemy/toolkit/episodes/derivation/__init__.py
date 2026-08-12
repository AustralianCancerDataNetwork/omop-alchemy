"""Construct episodes and resolve the relationships between them.

Episodes in the CDM are rows that reference each other through
``episode_parent_id`` and reach clinical facts through ``Episode_Event``.
Turning that into something queryable — a regimen with its cycles, a
diagnosis with the treatment that followed — is this tier's job: queries
that select episodes by concept, join parent and child episodes into a
single result so a hierarchy can be read in one pass, and establish the
date windows relating one episode to another, written against the raw
``Episode``/``Episode_Event`` tables rather than any materialised view.

Not yet populated in this package. The equivalent built against
materialised-view subclasses lives in ``omop-constructs``; nothing has
moved into ``toolkit`` yet.
"""
