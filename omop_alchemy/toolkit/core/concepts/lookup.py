"""Build and resolve scoped text-to-OMOP concept lookup indexes.

The database-facing source materialises a deliberately bounded vocabulary
selection once; the runtime resolver then performs only in-memory
normalisation and correction. Keeping those responsibilities separate is
important for bulk ETL, where a resolver must not reopen relationships or
expand vocabulary hierarchies per row.
"""

from typing import Iterable, Callable
from dataclasses import dataclass
from functools import cached_property
import sqlalchemy as sa
import sqlalchemy.orm as so

from .normalizers import normalize_default
from omop_alchemy.cdm.model import ConceptRow
from omop_alchemy.cdm.model.vocabulary import Concept, Concept_Synonym, Concept_Ancestor
from omop_alchemy.cdm.query import ConceptFilter

Normaliser = Callable[[str], str]


@dataclass(frozen=True)
class LookupIndex:
    """
    Materialised lookup table from normalised text keys to OMOP concept IDs.

    A LookupIndex is the *runtime artifact* produced by a LookupSpec and a
    ConceptSource. It represents a flat, precomputed mapping from one or more
    normalised string representations (e.g. concept names, codes, synonyms)
    to OMOP concept IDs.

    Attributes
    ----------
    name:
        Human-readable identifier for the lookup.
    unknown:
        Concept ID to return when a lookup fails, or None if failures should
        propagate as null.
    mapping:
        Dictionary mapping normalised string keys to OMOP concept IDs.
        Keys are expected to already be normalised at build time.

    Notes
    -----
    The mapping may contain multiple textual representations pointing to the
    same concept ID (e.g. name + code + synonym).
    """

    name: str
    unknown: int | None
    mapping: dict[str, int]

    def lookup(self, term: str | None) -> int | None:
        """Resolve an already-normalized key, returning the configured fallback."""
        if term is None:
            term = ""
        return self.mapping.get(term, self.unknown)

    def __contains__(self, item: str | int) -> bool:
        """Test membership by indexed key or by reachable concept ID."""
        if isinstance(item, str):
            return item in self.mapping
        if isinstance(item, int):
            return item in self.mapping.values()
        return False

    def __repr__(self) -> str:
        return (
            f"<LookupIndex name={self.name!r} "
            f"keys={len(self.mapping)} "
            f"concepts={len(self.all_concepts)} "
            f"unknown={self.unknown}>"
        )

    @property
    def all_concepts(self) -> set[int]:
        """Return the concept IDs represented by this materialized index."""
        return set(self.mapping.values())


@dataclass(frozen=True)
class LookupSpec:
    """
    Declarative specification for constructing a vocabulary lookup index.

    A LookupSpec defines *what* concepts should be included in a lookup and
    *which textual representations* should be indexed for resolution.

    The spec is consumed by a OMOPConceptSource, which materialises a LookupIndex
    by querying an OMOP vocabulary source and extracting the requested fields.

    This separation allows lookup semantics (domain, vocabulary, hierarchy,
    standardness, synonyms, normalisation) to be expressed explicitly and
    versioned independently of runtime resolution logic.

    Attributes
    ----------
    name:
        Stable identifier for this lookup specification.
    unknown:
        Concept ID to return for unmatched terms. Set to None to preserve
        nulls, or to a sentinel concept ID to force closed-world behaviour.
    domain_id:
        Optional OMOP domain filter
    concept_class_id:
        Optional list of OMOP concept_class_id values to restrict the lookup
    vocabulary_id:
        Optional list of OMOP vocabulary_id values to restrict the lookup
    require_standard:
        If True, restricts the lookup to concepts carrying a standardness flag.
        Named to match ``ConceptFilter.require_standard``, which it delegates to.
    include_classification:
        Widens ``require_standard`` to admit classification ('C') concepts.
        Defaults to True: a lookup exists to *recognise* vocabulary terms, and a
        classification concept is a legitimate thing to recognise even though it
        is not a valid mapping target. Set False for selection-shaped lookups.
    require_active:
        If True, excludes concepts with an ``invalid_reason``. Defaults to False
        so recognition stays permissive — a deprecated concept that matches the
        text can still be resolved forward through "Maps to" / "Concept replaced
        by", whereas filtering it out here discards the term entirely.
    code_filter:
        Optional substring filter applied to concept_code (ILIKE-based).
        Useful for coarse scoping (e.g. AJCC-only codes).
    parents:
        Optional list of ancestor concept IDs from which to expand the lookup
        via the Concept_Ancestor table.
    include_non_standard_descendants:
        If True, includes non-standard concepts when expanding from parents.
        Has no effect if parents is None.
    include_synonyms:
        If True, include Concept_Synonym entries in the lookup keys.
    normalizer:
        Function applied to all indexed strings at build time. This should
        match (or be compatible with) the normalisation used at resolution
        time by ConceptResolver.
    include:
        Tuple of ConceptRow attribute names to index as keys (e.g.
        ("concept_name", "concept_code")). This controls which textual fields
        become resolvable inputs.

    Notes
    -----
    - LookupSpec encodes *semantic intent*; LookupIndex encodes *runtime state*.
    - Specs are designed to be stable, inspectable configuration objects that
      can be versioned and reviewed as part of phenotype or ETL definitions.
    - Normalisation and correction policies are intentionally split between
      build-time (this spec) and runtime (ConceptResolver) to make lookup
      behaviour explicit and testable.
    """

    name: str
    unknown: int | None = 0
    domain_id: str | None = None
    concept_class_id: list[str] | None = None
    vocabulary_id: list[str] | None = None
    require_standard: bool = True
    include_classification: bool = True
    require_active: bool = False
    code_filter: str | None = None
    parents: list[int] | None = None
    include_non_standard_descendants: bool = False
    include_synonyms: bool = False
    normalizer: Normaliser = normalize_default
    include: tuple[str, ...] = ("concept_name", "concept_code")  # index fields


class OMOPConceptSource:
    """
    Concrete ConceptSource backed by OMOP CDM vocabulary tables.

    It is a thin, explicit adapter between SQLAlchemy + OMOP CDM
    and higher-level vocabulary indexing logic.


    Used exclusively to builds a query based on provided parameters (adds
    filter for each non-None parameter, and joins to Concept_Ancestor
    if parents are specified).
    """

    @staticmethod
    def fetch_synonyms(
        session: so.Session,
        *,
        concept_ids: Iterable[int] | None = None,
    ) -> list[tuple[int, str]]:
        """
        Return (concept_id, synonym) pairs for concept synonyms.

        The join to ``concept`` scopes the result to synonyms whose concept
        actually exists, and *concept_ids* narrows it further to a caller-supplied
        set. Both are applied in SQL: without them this streams every synonym row
        in the vocabulary back to Python to be discarded, which on a full Athena
        load is millions of rows for a lookup covering a handful of domains.
        """
        query = sa.select(
            Concept_Synonym.concept_id,
            Concept_Synonym.concept_synonym_name,
        ).join(Concept, Concept.concept_id == Concept_Synonym.concept_id)

        if concept_ids is not None:
            query = query.where(Concept_Synonym.concept_id.in_(list(concept_ids)))

        rows = session.execute(query).all()

        return [
            (int(r.concept_id), r.concept_synonym_name)
            for r in rows
            if r.concept_synonym_name
        ]

    @staticmethod
    def fetch_concepts(
        session: so.Session,
        *,
        domain_id: str | None = None,
        concept_class_id: Iterable[str] | None = None,
        vocabulary_id: Iterable[str] | None = None,
        require_standard: bool = True,
        include_classification: bool = True,
        require_active: bool = False,
        code_filter: str | None = None,
        parents: Iterable[int] | None = None,
        include_non_standard_descendants: bool = False,
    ) -> list[ConceptRow]:
        """
        Fetch concepts matching the provided constraints.

        This method supports two primary modes:
        1. Flat filtering by domain / class / vocabulary
        2. Hierarchical expansion from parent concept(s)

        Domain, vocabulary, standardness and validity are delegated to
        :class:`~omop_alchemy.cdm.query.ConceptFilter` so this layer shares one
        implementation of the OMOP flag rules with the rest of the package. In
        particular the flag comparisons are normalised, so a blank or
        whitespace-padded ``standard_concept`` is classified the same way here as
        it is by ``Concept.is_standard``.

        Recognition is permissive by default, because this layer resolves *text
        to a concept*, not a concept to a mapping target. Both defaults follow
        from that:

        - *include_classification* is ``True`` — classification concepts are
          legitimate things to recognise, even though they are not valid mapping
          targets.
        - *require_active* is ``False`` — a deprecated concept that matches the
          text is a useful hit, because the caller can follow ``Maps to`` /
          ``Concept replaced by`` to a valid successor. Filtering it out at
          recognition time discards the term entirely, with nothing to resolve
          forward from.

        Pass ``include_classification=False`` / ``require_active=True`` when the
        index feeds concept *selection* rather than recognition, where the strict
        OMOP mapping-target rules apply.
        """

        parents = list(parents) if parents else None
        # Standardness is dropped only for an explicitly non-standard descendant
        # expansion; every other combination keeps it.
        apply_standard = require_standard and not (
            parents and include_non_standard_descendants
        )

        query = sa.select(Concept)
        if parents:
            query = query.join(
                Concept_Ancestor,
                Concept_Ancestor.descendant_concept_id == Concept.concept_id,
            ).where(Concept_Ancestor.ancestor_concept_id.in_(parents))

        query = ConceptFilter(
            domains=(domain_id,) if domain_id else None,
            vocabularies=tuple(vocabulary_id) if vocabulary_id else None,
            require_standard=apply_standard,
            include_classification=include_classification,
            require_active=require_active,
        ).apply(query)

        if concept_class_id:
            query = query.where(Concept.concept_class_id.in_(list(concept_class_id)))
        if code_filter:
            query = query.where(Concept.concept_code.ilike(f"%{code_filter}%"))

        rows = session.execute(query).scalars().all()
        return [
            ConceptRow(
                concept_id=int(r.concept_id),
                concept_name=r.concept_name,
                concept_code=r.concept_code,
                domain_id=r.domain_id,
                concept_class_id=r.concept_class_id,
                vocabulary_id=r.vocabulary_id,
                standard_concept=r.standard_concept,
            )
            for r in rows
        ]

    @staticmethod
    def descendants(
        session: so.Session,
        parents: list[int],
        *,
        include_non_standard: bool = False,
    ) -> list[int]:
        """Return descendant IDs using the source's standardness policy."""
        rows = OMOPConceptSource.fetch_concepts(
            session,
            parents=parents,
            include_non_standard_descendants=include_non_standard,
            require_standard=not include_non_standard,
        )
        return list({r.concept_id for r in rows})

    @staticmethod
    def build_lookup(
        session: so.Session,
        spec: LookupSpec,
    ) -> LookupIndex:
        """Materialize one scoped lookup and its optional synonym keys.

        The returned index is intentionally detached from the session. If
        multiple selected representations normalize to the same key, the
        later materialized assignment wins; callers that need collision-free
        semantics should narrow the ``LookupSpec`` rather than rely on row
        ordering.
        """
        rows = OMOPConceptSource.fetch_concepts(
            session,
            domain_id=spec.domain_id,
            concept_class_id=spec.concept_class_id,
            vocabulary_id=spec.vocabulary_id,
            require_standard=spec.require_standard,
            include_classification=spec.include_classification,
            require_active=spec.require_active,
            code_filter=spec.code_filter,
            parents=spec.parents,
            include_non_standard_descendants=spec.include_non_standard_descendants,
        )

        ids = {r.concept_id for r in rows}

        m: dict[str, int] = {}
        for r in rows:
            if "concept_name" in spec.include and r.concept_name:
                m[spec.normalizer(r.concept_name)] = r.concept_id
            if "concept_code" in spec.include and r.concept_code:
                m[spec.normalizer(r.concept_code)] = r.concept_id

        if spec.include_synonyms:
            for cid, syn in OMOPConceptSource.fetch_synonyms(session, concept_ids=ids):
                if syn:
                    m[spec.normalizer(syn)] = cid

        return LookupIndex(name=spec.name, unknown=spec.unknown, mapping=m)


class ConceptResolver:
    """
    Runtime resolver for mapping free-text terms to OMOP concept IDs.

    A ConceptResolver wraps a pre-built LookupIndex and applies runtime
    normalisation and optional correction passes to resolve arbitrary
    input strings to concept IDs. It is intentionally lightweight and
    stateless: all semantic scope and vocabulary constraints are encoded
    upstream in the LookupSpec and LookupIndex.

    Resolution proceeds in ordered stages:
    1. Apply the primary normaliser to the input term and attempt a direct lookup.
    2. If no hit is found, apply each correction function in turn, re-normalise,
       and retry the lookup.
    3. If no match is found, return the configured ``unknown`` concept ID.

    This design allows simple, explicit handling of common data quality issues
    (e.g. formatting differences, legacy codes, mild normalisation errors)
    without introducing fuzzy matching, probabilistic scoring, or hidden
    inference logic.

    Parameters
    ----------
    index:
        Pre-built LookupIndex providing the normalised key → concept_id mapping.
    normalizer:
        Optional normalisation function applied to input terms at lookup time.
        Defaults to ``normalize_default``. This should be compatible with the
        normaliser used when constructing the LookupIndex.
    corrections:
        Optional ordered list of correction functions applied to the raw input
        term prior to normalisation and lookup. Each correction is tried in
        sequence until a match is found.

    Notes
    -----
    - ConceptResolver performs no database access and no dynamic expansion of
      vocabularies; it operates over the materialised LookupIndex.
    - Resolution is deterministic and transparent: there is no fuzzy matching,
      ranking, or probabilistic inference.
    - Correction functions are applied conservatively and in-order; later
      corrections do not override earlier successful matches.
    - ``lookup_exact`` bypasses correction passes and performs a single
      normalised lookup, which is useful for validation and debugging.

    Examples
    --------
    >>> resolver = ConceptResolver(index)
    >>> resolver.lookup("Stage III")
    123456
    >>> resolver.lookup("stage-3")
    123456

    """

    def __init__(
        self,
        index: LookupIndex,
        *,
        normalizer: Normaliser | None = None,
        corrections: list[Callable[[str], str]] | None = None,
    ):
        """Bind a materialized index to runtime normalization and corrections."""
        self.index = index
        self._normalizer = normalizer or normalize_default
        self._corrections = corrections or []

    def lookup(self, term: str | None) -> int | None:
        """Resolve a term, trying direct lookup before ordered corrections."""
        if not term:
            return self.index.unknown

        key = self._normalizer(term)
        hit = self.index.mapping.get(key)
        if hit is not None:
            return hit

        for corr in self._corrections:
            key2 = self._normalizer(corr(term))
            hit = self.index.mapping.get(key2)
            if hit is not None:
                return hit

        return self.index.unknown

    def lookup_exact(self, term: str | None) -> int | None:
        """Resolve only the normalized input, bypassing correction functions."""
        if not term:
            return self.index.unknown
        return self.index.mapping.get(self._normalizer(term), self.index.unknown)

    def __contains__(self, item: str | int) -> bool:
        """Test corrected text membership or direct concept-ID membership."""
        if isinstance(item, int):
            return item in self.all_concepts
        if isinstance(item, str):
            return self.lookup(item) != self.index.unknown
        return False

    @cached_property
    def all_concepts(self) -> set[int]:
        """Every concept ID reachable through this resolver's index.

        Cached: the index is fixed at construction, and callers legitimately
        union several resolvers' sets, which previously rebuilt each one per
        access.  Returned by reference, so treat it as read-only.
        """
        return set(self.index.mapping.values())

    def estimated_bytes(self) -> int:
        """Approximate retained size, for cache accounting.

        A name/code/synonym index costs several times more per concept than a
        bare ID set, which is why the cache bound is measured in bytes rather
        than entry counts.  Measured at ~350 bytes per concept for OMOP-length
        names and codes with one synonym each.
        """
        return 350 * len(self.index.mapping)

    def __repr__(self) -> str:
        return (
            f"<ConceptResolver name={self.index.name!r} "
            f"concepts={len(self.all_concepts)} "
            f"corrections={len(self._corrections)}>"
        )


def make_concept_resolver(
    session: so.Session,
    *,
    name: str,
    unknown: int | None = 0,
    domain_id: str | None = None,
    concept_class_id: list[str] | None = None,
    vocabulary_id: list[str] | None = None,
    require_standard: bool = True,
    include_classification: bool = True,
    require_active: bool = False,
    code_filter: str | None = None,
    parents: list[int] | None = None,
    include_non_standard_descendants: bool = False,
    include_synonyms: bool = False,
    include: tuple[str, ...] = ("concept_name", "concept_code"),
    build_normalizer: Normaliser = normalize_default,
    runtime_normalizer: Normaliser | None = None,
    corrections: list[Callable[[str], str]] | None = None,
) -> ConceptResolver:
    """
    Convenience factory for constructing a ConceptResolver from declarative inputs.

    This function bundles the common workflow of:
    - defining a LookupSpec
    - materialising a LookupIndex from OMOP
    - constructing a ConceptResolver for runtime use

    Parameters
    ----------
    session:
        Active SQLAlchemy session connected to the OMOP CDM database.
    name:
        Stable identifier for this lookup specification, used in logging and debugging.
    unknown:
        Concept ID to return for unmatched terms. Set to None to preserve nulls, or to
        a sentinel concept ID to force closed-world behaviour.
    domain_id:
        Optional OMOP domain filter for the concepts to include in the lookup.
    concept_class_id:
        Optional list of OMOP concept_class_id values to restrict the lookup.
    vocabulary_id:
        Optional list of OMOP vocabulary_id values to restrict the lookup.
    require_standard:
        If True, restricts the lookup to concepts carrying a standardness flag.
        Named to match ``ConceptFilter.require_standard``, which it delegates to.
    include_classification:
        Widens ``require_standard`` to admit classification ('C') concepts.
        Defaults to True: a lookup exists to *recognise* vocabulary terms, and a
        classification concept is a legitimate thing to recognise even though it
        is not a valid mapping target. Set False for selection-shaped lookups.
    require_active:
        If True, excludes concepts with an ``invalid_reason``. Defaults to False
        so recognition stays permissive — a deprecated concept that matches the
        text can still be resolved forward through "Maps to" / "Concept replaced
        by", whereas filtering it out here discards the term entirely.
    code_filter:
        Optional substring filter applied to concept_code (ILIKE-based).
        Useful for coarse scoping (e.g. AJCC-only codes).
    parents:
        Optional list of ancestor concept IDs from which to expand the lookup
        via the Concept_Ancestor table.
    include_non_standard_descendants:
        If True, includes non-standard concepts when expanding from parents. Has no
        effect if `parents` is None.
    include_synonyms:
        If True, include Concept_Synonym entries in the lookup keys.
    include:
        Tuple of ConceptRow attribute names to index as keys (e.g. ("concept_name",
        "concept_code")). This controls which textual fields become resolvable inputs.
    build_normalizer:
        Normalisation function applied to all indexed strings at build time. This should
        match (or be compatible with) the normaliser used at resolution time by
        ConceptResolver.
    runtime_normalizer:
        Optional normalisation function applied to input terms at lookup time. Defaults to
        ``normalize_default``. This should be compatible with the normaliser used when
        constructing the LookupIndex.
    corrections:
        Optional ordered list of correction functions applied to the raw input term prior
        to normalisation and lookup
    """

    spec = LookupSpec(
        name=name,
        unknown=unknown,
        domain_id=domain_id,
        concept_class_id=concept_class_id,
        vocabulary_id=vocabulary_id,
        require_standard=require_standard,
        include_classification=include_classification,
        require_active=require_active,
        code_filter=code_filter,
        parents=parents,
        include_non_standard_descendants=include_non_standard_descendants,
        include_synonyms=include_synonyms,
        normalizer=build_normalizer,
        include=include,
    )

    index = OMOPConceptSource.build_lookup(session, spec)

    return ConceptResolver(
        index,
        normalizer=runtime_normalizer or build_normalizer,
        corrections=corrections,
    )
