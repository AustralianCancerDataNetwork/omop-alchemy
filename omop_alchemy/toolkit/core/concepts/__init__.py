"""Resolve free-text terms and source codes to OMOP concept IDs.

Source data rarely arrives with concept IDs attached.  This module turns a
declarative description of *which* concepts are eligible into a runtime
resolver that maps incoming text to those concepts, applying the same
normalisation on both sides so that matching is predictable.

Three pieces make up the workflow:

``LookupSpec``
    Declares which concepts belong in a lookup — by vocabulary, domain,
    concept class, or explicit ancestry — and which text fields are
    indexed.

``LookupIndex``
    The materialised table of normalised text keys to concept IDs that a
    spec produces against the vocabulary tables.

``ConceptResolver``
    Wraps an index and resolves terms at runtime, applying the same
    normalisation used to build the keys.

``make_concept_resolver`` bundles all three for the common case::

    from omop_alchemy.toolkit.core.concepts import make_concept_resolver

    resolver = make_concept_resolver(
        session,
        name="condition lookup",
        domain_id="Condition",
    )
    concept_id = resolver.lookup("Adenocarcinoma of lung")

Normalisation is composable.  ``compose_normalizers`` chains individual
rules — ``normalize_default`` for whitespace and casing, ``strip_uicc``
and ``make_stage`` for staging text, ``site_to_NOS`` for site
generalisation — so that a resolver's matching behaviour is stated
explicitly rather than implied.

Building an index queries the vocabulary tables, so resolvers are worth
reusing.  ``ConceptResolverRegistry`` constructs each resolver on first
access and caches it for the registry's lifetime.

Governed concept sets are the other half of this module.  Where a resolver maps
*text* to concepts, a concept group answers whether a *concept ID* belongs to a
governed clinical set::

    from omop_alchemy.toolkit.core.concepts import (
        ConceptGroupSpec,
        resolve_concept_group,
    )

    RT = ConceptGroupSpec(name="radiotherapy", unit=...)   # governed anchors

    group = resolve_concept_group(session, RT)
    procedure.procedure_concept_id in group        # Python, O(1)
    group.expression(Procedure_Occurrence.procedure_concept_id)   # SQL

Both access paths derive from one spec, so they cannot disagree.  Expansions are
cached per *vocabulary*, not per engine, so recreating an engine against the same
database does not re-run the closure queries — see :mod:`.identity` for how an
engine declares which vocabulary it reads, and note that a caller building its
own engines must register that itself.

Nothing here touches a database at import time: specs are declarative and
registries build on first request.
"""

from .groups import (
    ConceptGroupSpec,
    ResolvedConceptGroup,
    build_concept_group,
)
from .identity import (
    clear_vocabulary_identity,
    register_vocabulary_identity,
)
from .lookup import (
    ConceptResolver,
    LookupIndex,
    LookupSpec,
    OMOPConceptSource,
    make_concept_resolver,
)
from .normalizers import (
    compose_normalizers,
    make_stage,
    normalize_default,
    site_to_NOS,
    strip_uicc,
)
from .registry import (
    DEFAULT_MAX_CACHE_BYTES,
    CacheStats,
    ConceptGroupRegistry,
    ConceptResolverRegistry,
    clear_concept_group_cache,
    concept_group_cache_stats,
    concept_group_registry,
    resolve_concept_group,
)
from .runtime import (
    RuntimeConceptSetSpec,
    descendant_concept_select,
    runtime_concept_predicate,
)

__all__ = [
    "DEFAULT_MAX_CACHE_BYTES",
    "CacheStats",
    "ConceptGroupRegistry",
    "ConceptGroupSpec",
    "ConceptResolver",
    "ConceptResolverRegistry",
    "LookupIndex",
    "LookupSpec",
    "OMOPConceptSource",
    "ResolvedConceptGroup",
    "RuntimeConceptSetSpec",
    "build_concept_group",
    "clear_concept_group_cache",
    "clear_vocabulary_identity",
    "compose_normalizers",
    "concept_group_cache_stats",
    "concept_group_registry",
    "make_concept_resolver",
    "make_stage",
    "normalize_default",
    "register_vocabulary_identity",
    "resolve_concept_group",
    "descendant_concept_select",
    "runtime_concept_predicate",
    "site_to_NOS",
    "strip_uicc",
]
