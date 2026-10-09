"""Cache scope for concept-set expansions: the vocabulary a session reads.

An expansion depends only on the vocabulary tables, so engines reading the same
physical database and vocabulary schema share one scope. The scope is derived
from the engine itself: ``connection_key`` of its URL plus the physical schema
its ``schema_translate_map`` gives the ``vocab`` tag.

An engine that cannot name its vocabulary that way gets a scope of its own: an
in-memory SQLite database, where two engines on one URL are separate databases,
or an engine with no ``vocab`` entry in its translate map.
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role, UnregisteredSchemaTagError, connection_key, is_ephemeral_url, physical_schema_of


def vocabulary_engine_of(session: so.Session) -> sa.Engine:
    """Engine *session* sends vocabulary statements to.

    Asked through ``Concept`` rather than with no argument, so a session
    binding each table to its own engine answers with the vocabulary one.
    """
    from omop_alchemy.cdm.model.vocabulary.concept import Concept

    return session.get_bind(Concept).engine


def cache_scope(session: so.Session) -> str | sa.Engine:
    """Cache scope for *session*: its physical vocabulary, else its vocabulary engine.

    A ``str`` scope is shared by every engine reading that vocabulary. An
    ``Engine`` scope is private to that engine and dies with it.
    """
    engine = vocabulary_engine_of(session)
    if is_ephemeral_url(engine.url):
        return engine
    try:
        schema = physical_schema_of(engine, schema_tag=Role.VOCAB.value)
    except UnregisteredSchemaTagError:
        return engine
    return f"{connection_key(engine.url)}|{schema or ''}"
