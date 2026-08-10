from typing import Iterable, Callable
from dataclasses import dataclass
from functools import cached_property
import sqlalchemy as sa
import sqlalchemy.orm as so

from .normalizers import normalize_default
from omop_alchemy.cdm.model import ConceptRow
from omop_alchemy.cdm.model.vocabulary import Concept, Concept_Synonym, Concept_Ancestor

"""
Class definitions for vocabulary handling and mapping.

This is somewhat redunant with some of omop-graph but 
mutual dependency is awkward and this is a thin layer so 
it's not worth over-engineering separation at this stage.
"""

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
        if term is None:
            term = ""
        return self.mapping.get(term, self.unknown)

    def __contains__(self, item: str | int) -> bool:
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
    standard_only:
        If True, restricts the lookup to standard concepts only.
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
    standard_only: bool = True
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
    def fetch_synonyms(session: so.Session) -> list[tuple[int, str]]:
        """
        Return (concept_id, synonym) pairs for all concept synonyms.

        Filtering (standard / domain / etc.) is intentionally left
        to higher layers.
        """
        rows = session.execute(
            sa.select(
                Concept_Synonym.concept_id,
                Concept_Synonym.concept_synonym_name,
            )
        ).all()

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
        standard_only: bool = True,
        code_filter: str | None = None,
        parents: Iterable[int] | None = None,
        include_non_standard_descendants: bool = False,
    ) -> list[ConceptRow]:
        """
        Fetch concepts matching the provided constraints.

        This method supports two primary modes:
        1. Flat filtering by domain / class / vocabulary
        2. Hierarchical expansion from parent concept(s)

        """

        q = session.query(Concept)
        if parents:
            parents = list(parents)
            q = (
                q.join(
                    Concept_Ancestor,
                    Concept_Ancestor.descendant_concept_id == Concept.concept_id,
                )
                .filter(Concept_Ancestor.ancestor_concept_id.in_(parents))
            )
            if standard_only and not include_non_standard_descendants:
                q = q.filter(Concept.standard_concept == "S")
        if domain_id:
            q = q.filter(Concept.domain_id == domain_id)
        if concept_class_id:
            q = q.filter(Concept.concept_class_id.in_(list(concept_class_id)))
        if vocabulary_id:
            q = q.filter(Concept.vocabulary_id.in_(list(vocabulary_id)))
        if standard_only and not parents:
            q = q.filter(Concept.standard_concept == "S")
        if code_filter:
            q = q.filter(Concept.concept_code.ilike(f"%{code_filter}%"))
        rows = q.all()
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
        rows = OMOPConceptSource.fetch_concepts(
            session,
            parents=parents,
            include_non_standard_descendants=include_non_standard,
            standard_only=not include_non_standard,
        )
        return list({r.concept_id for r in rows})
    

    @staticmethod
    def build_lookup(
        session: so.Session,
        spec: LookupSpec,
    ) -> LookupIndex:
        rows = OMOPConceptSource.fetch_concepts(
            session,
            domain_id=spec.domain_id,
            concept_class_id=spec.concept_class_id,
            vocabulary_id=spec.vocabulary_id,
            standard_only=spec.standard_only,
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
            for cid, syn in OMOPConceptSource.fetch_synonyms(session):
                if cid in ids and syn:
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
        self.index = index
        self._normalizer = normalizer or normalize_default
        self._corrections = corrections or []

    def lookup(self, term: str | None) -> int | None:
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
        if not term:
            return self.index.unknown
        return self.index.mapping.get(self._normalizer(term), self.index.unknown)

    def __contains__(self, item: str | int) -> bool:
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
    standard_only: bool = True,
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
    standard_only:
        If True, restricts the lookup to standard concepts only.
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
        standard_only=standard_only,
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
