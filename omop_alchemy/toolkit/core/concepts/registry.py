from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Generic, Protocol, TypeVar
from weakref import WeakKeyDictionary

import sqlalchemy as sa
import sqlalchemy.orm as so

from .groups import ConceptGroupSpec, ResolvedConceptGroup, build_concept_group
from .identity import cache_scope
from .lookup import ConceptResolver

logger = logging.getLogger(__name__)

#: Default cache ceiling. Deliberately generous: eviction re-runs vocabulary
#: queries, so the bound is a safety net against a pathological concept set,
#: not a working-set manager. See ``CacheStats.rebuilds_after_evict`` for the
#: signal that it is set too low.
DEFAULT_MAX_CACHE_BYTES = 512 * 1024 * 1024


class SizedPayload(Protocol):
    """A cacheable concept artifact that can report its retained size."""

    def estimated_bytes(self) -> int: ...


T = TypeVar("T", bound=SizedPayload)


@dataclass
class CacheStats:
    """Observability for a bounded registry.

    ``rebuilds_after_evict`` is the load-bearing number.  Evicting an entry
    that is never asked for again is exactly what a bound is for; evicting one
    that is then rebuilt is thrashing, and it is otherwise invisible because it
    presents as ordinary slowness rather than as a cache problem.  While it
    stays at zero the bound is correct.
    """

    entries: int = 0
    cached_bytes: int = 0
    evictions: int = 0
    rebuilds_after_evict: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "entries": self.entries,
            "cached_bytes": self.cached_bytes,
            "evictions": self.evictions,
            "rebuilds_after_evict": self.rebuilds_after_evict,
        }


class _LazyBoundedRegistry(Generic[T]):
    """Lazy named builders with an LRU bound measured in bytes.

    Builders are registered up front and invoked on first request, so
    registering costs nothing and importing a module that declares concept sets
    touches no database.

    The bound is on estimated bytes rather than entry count: payload densities
    differ by several times per concept ID, so a count-based bound would
    silently let the densest payload consume most of the budget while appearing
    to use its share.
    """

    def __init__(
        self,
        engine: sa.Engine,
        *,
        max_bytes: int = DEFAULT_MAX_CACHE_BYTES,
    ) -> None:
        self.engine = engine
        self.max_bytes = max_bytes
        self._cache: "OrderedDict[str, T]" = OrderedDict()
        self._builders: dict[str, Callable[[so.Session], T]] = {}
        self._evicted: set[str] = set()
        self.stats = CacheStats()

    def register(self, name: str, builder: Callable[[so.Session], T]) -> None:
        """Record how to build ``name``, without building it."""
        if name in self._builders:
            raise KeyError(f"Resolver '{name}' is already registered")
        self._builders[name] = builder

    def get(self, name: str) -> T:
        """Return ``name``, building and caching it on first request.

        Builds in a **new** session on this registry's engine rather than in a
        caller's session, so populating the cache never joins or affects a
        caller's transaction. The consequence is that uncommitted data is not
        visible: correct for vocabulary tables, which are committed reference
        data, but it means a test wanting to exercise expansion over
        uncommitted rows should call the builder directly with its own session.
        """
        if name in self._cache:
            self._cache.move_to_end(name)
            return self._cache[name]

        if name not in self._builders:
            raise KeyError(
                f"No resolver named '{name}' is registered. "
                f"Available resolvers: {sorted(self._builders)}"
            )

        if name in self._evicted:
            self.stats.rebuilds_after_evict += 1
            self._evicted.discard(name)
            logger.info(
                "concept cache: rebuilding %r after eviction "
                "(rebuilds_after_evict=%d, max_bytes=%d) - the cache bound may be too low",
                name,
                self.stats.rebuilds_after_evict,
                self.max_bytes,
            )

        with so.Session(self.engine) as session:
            value = self._builders[name](session)

        self._store(name, value)
        return value

    def _store(self, name: str, value: T) -> None:
        self._cache[name] = value
        self.stats.entries = len(self._cache)
        self.stats.cached_bytes += value.estimated_bytes()
        self._evict_to_bound()

    def _evict_to_bound(self) -> None:
        while self.stats.cached_bytes > self.max_bytes and len(self._cache) > 1:
            evicted_name, evicted = self._cache.popitem(last=False)
            self.stats.cached_bytes -= evicted.estimated_bytes()
            self.stats.entries = len(self._cache)
            self.stats.evictions += 1
            self._evicted.add(evicted_name)
            logger.warning(
                "concept cache: evicted %r (%d bytes) to stay under %d bytes; "
                "cached_bytes now %d across %d entries",
                evicted_name,
                evicted.estimated_bytes(),
                self.max_bytes,
                self.stats.cached_bytes,
                self.stats.entries,
            )

    def clear(self) -> None:
        self._cache.clear()
        self._evicted.clear()
        self.stats = CacheStats()

    def __getitem__(self, name: str) -> T:
        return self.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._builders


class ConceptResolverRegistry(_LazyBoundedRegistry[ConceptResolver]):
    """
    Lazy registry for ConceptResolvers.

    Resolvers are constructed on first access and cached for the lifetime
    of this registry instance. The registry is scoped to a SQLAlchemy Engine,
    ensuring vocab lookups are built once per database.
    """


class ConceptGroupRegistry(_LazyBoundedRegistry[ResolvedConceptGroup]):
    """Lazy registry for governed concept groups, scoped to one vocabulary.

    Obtain one through :func:`concept_group_registry` rather than constructing
    it directly, so registries are shared per vocabulary identity instead of
    per engine.
    """

    def register_spec(self, spec: ConceptGroupSpec) -> None:
        """Register ``spec`` under its governed name, if not already present."""
        if spec.name in self._builders:
            return
        self.register(spec.name, lambda session: build_concept_group(session, spec))


_BY_IDENTITY: dict[str, ConceptGroupRegistry] = {}
_BY_ENGINE: "WeakKeyDictionary[sa.Engine, ConceptGroupRegistry]" = WeakKeyDictionary()


def concept_group_registry(session: so.Session) -> ConceptGroupRegistry:
    """Return the group registry for the vocabulary behind ``session``.

    Registries are keyed on vocabulary identity where one has been registered
    (see :mod:`.identity`), so recreating an engine against the same dataset
    reuses expansions.  Otherwise they are keyed weakly on the engine, which is
    still built-once-per-engine but is not shared across engines.

    In-memory SQLite intentionally lands in the second case: two such engines
    are separate databases despite identical configuration, so cross-engine
    sharing would serve one database's concept sets for another.
    """
    scope = cache_scope(session)
    if isinstance(scope, str):
        registry = _BY_IDENTITY.get(scope)
        if registry is None:
            registry = ConceptGroupRegistry(session.get_bind().engine)
            _BY_IDENTITY[scope] = registry
        return registry

    registry = _BY_ENGINE.get(scope)
    if registry is None:
        registry = ConceptGroupRegistry(scope)
        _BY_ENGINE[scope] = registry
    return registry


def resolve_concept_group(
    session: so.Session,
    spec: ConceptGroupSpec,
) -> ResolvedConceptGroup:
    """Expand ``spec`` against ``session``'s vocabulary, cached.

    The cache key is ``spec.name`` within the vocabulary's registry, so it
    derives from the governed semantic-unit name rather than a locally invented
    label.
    """
    registry = concept_group_registry(session)
    registry.register_spec(spec)
    return registry.get(spec.name)


def clear_concept_group_cache() -> None:
    """Drop every cached group expansion, across all vocabularies.

    Per-vocabulary keying means this is rarely needed — moving database gives a
    different identity and therefore a different registry.  It remains an escape
    hatch for a dataset reloaded in place under an unchanged identity.
    """
    for registry in _BY_IDENTITY.values():
        registry.clear()
    for registry in _BY_ENGINE.values():
        registry.clear()
    _BY_IDENTITY.clear()
    _BY_ENGINE.clear()


def concept_group_cache_stats() -> dict[str | int, dict[str, int]]:
    """Per-scope cache statistics, for monitoring the bound.

    Watch ``rebuilds_after_evict``: while it is zero the shared bound is
    holding, and if it climbs the per-scope byte totals are the evidence for
    raising it or splitting the budget by payload kind.
    """
    stats: dict[str | int, dict[str, int]] = {
        identity: registry.stats.as_dict()
        for identity, registry in _BY_IDENTITY.items()
    }
    for engine, registry in _BY_ENGINE.items():
        stats[id(engine)] = registry.stats.as_dict()
    return stats
