"""Vocabulary identity, so concept-set caches survive engine recreation.

An expanded concept set is a function of the *vocabulary* behind a connection,
not of the ``Engine`` object that happens to be open.  Caching per engine means
recreating an engine against the same database re-runs every closure query;
caching per URL is unsafe, because two in-memory SQLite engines share a URL and
are separate databases.

So a caller that knows which vocabulary an engine points at registers that fact:

    from omop_alchemy.toolkit.core.concepts import register_vocabulary_identity

    engine = my_own_factory(...)
    register_vocabulary_identity(engine, my_identity_string)

``omop_alchemy.config.create_cdm_engine`` does this automatically, but it is
**one registrar among several** — downstream packages build engines through
their own factories and must be able to register too, or their engines silently
fall back to per-engine caching and lose the reuse this exists to provide.
Compose the identity string with ``omop_alchemy.config.vocabulary_identity`` so
every caller spells the same dataset the same way; two spellings produce two
cache entries that each look authoritative.

Engines with no registered identity are cached per engine object, held weakly.
That is always correct — it just does not share across engines.
"""

from __future__ import annotations

from weakref import WeakKeyDictionary

import sqlalchemy as sa
import sqlalchemy.orm as so

_VOCAB_IDENTITY: "WeakKeyDictionary[sa.Engine, str]" = WeakKeyDictionary()


def register_vocabulary_identity(engine: sa.Engine, identity: str) -> None:
    """Declare which vocabulary dataset ``engine`` reads.

    Concept-set caches keyed on this identity are shared by every engine
    registered under it, so recreating an engine reuses the expansion.

    Pass the engine your factory *returns*.  ``ResolvedDatabase.create_engine``
    ends with ``execution_options(schema_translate_map=...)``, which yields a
    derived ``OptionEngine``; that is the object sessions bind to, and the one
    lookups will see.

    Do not register an identity for an ephemeral database — notably in-memory
    SQLite, where two engines built from identical configuration are genuinely
    separate databases.  Those correctly fall back to per-engine caching.
    """
    _VOCAB_IDENTITY[engine] = identity


def clear_vocabulary_identity(engine: sa.Engine) -> None:
    """Forget ``engine``'s registered identity, if it had one."""
    _VOCAB_IDENTITY.pop(engine, None)


def engine_for_bind(bind: sa.Engine | sa.Connection) -> sa.Engine:
    """Normalise a session bind to an ``Engine``.

    ``Session.get_bind()`` returns a ``Connection`` for connection-bound
    sessions, which is a normal pattern in test fixtures.  Without this, every
    such session would look like a distinct cache scope.
    """
    return bind.engine


def cache_scope(session: so.Session) -> str | sa.Engine:
    """Cache scope for ``session``: its vocabulary identity, else its engine.

    A ``str`` scope is shared across engines pointing at the same vocabulary.
    An ``Engine`` scope is private to that engine and dies with it.
    """
    engine = engine_for_bind(session.get_bind())
    return _VOCAB_IDENTITY.get(engine, engine)
