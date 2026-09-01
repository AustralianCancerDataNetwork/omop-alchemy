"""Governed concept groups: laziness, resolution, caching, and both access paths."""

from __future__ import annotations

import subprocess
import sys
import warnings
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
import sqlalchemy.orm as so

from omop_alchemy.toolkit.core.concepts import (
    ConceptGroupSpec,
    build_concept_group,
    clear_concept_group_cache,
    concept_group_cache_stats,
    concept_group_registry,
    register_vocabulary_identity,
    resolve_concept_group,
)
from omop_alchemy.toolkit.core.concepts.registry import ConceptGroupRegistry


@dataclass
class _Unit:
    """Stand-in for an omop-semantics RuntimeSemanticUnit."""

    parent_ids: set[int]
    excluded_parent_ids: set[int]
    exact_ids: set[int]


def _spec(name="test_group", parents=(1,), excluded=(), exact=(), **kw):
    return ConceptGroupSpec(
        name=name,
        unit=_Unit(set(parents), set(excluded), set(exact)),
        **kw,
    )


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_concept_group_cache()
    yield
    clear_concept_group_cache()


# ── laziness ────────────────────────────────────────────────────────────────

def test_importing_oncology_touches_no_database_or_semantics():
    """The oncology package must import with no database and no semantics runtime.

    Basic CDM work should not pay for oncology concept sets, and nothing in the
    chain may open an engine at import. Run in a subprocess so the assertion is
    about a cold interpreter rather than whatever this session has already
    imported.
    """
    code = (
        "import sys\n"
        "import omop_alchemy.toolkit.analytics.oncology as onc\n"
        "assert onc.RADIOTHERAPY_PROCEDURES.name == 'radiotherapy'\n"
        # declaring specs must not have pulled the semantics runtime in
        "assert 'omop_semantics.runtime.default_valuesets' not in sys.modules, 'semantics loaded at import'\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_spec_construction_performs_no_io():
    """Constructing a spec must not read its unit."""

    class Exploding:
        def __getattr__(self, name):
            raise AssertionError(f"unit was read at construction: {name}")

    ConceptGroupSpec(name="lazy", unit=Exploding())


# ── resolution semantics ────────────────────────────────────────────────────
# The fixture vocabulary ships an empty concept_ancestor, so these tests insert
# the ancestry they need. They call build_concept_group directly rather than
# going through the registry: the registry deliberately builds in its own
# session, so it would not see uncommitted rows.

ANC = sa.table(
    "concept_ancestor",
    sa.column("ancestor_concept_id"),
    sa.column("descendant_concept_id"),
    sa.column("min_levels_of_separation"),
    sa.column("max_levels_of_separation"),
)


def _ancestry(session, pairs):
    session.execute(
        ANC.insert(),
        [
            {
                "ancestor_concept_id": a,
                "descendant_concept_id": d,
                "min_levels_of_separation": 0 if a == d else 1,
                "max_levels_of_separation": 0 if a == d else 1,
            }
            for a, d in pairs
        ],
    )
    session.flush()


def test_group_resolves_descendants(session):
    """Parents expand through concept_ancestor."""
    _ancestry(session, [(100, 100), (100, 101), (100, 102)])
    group = build_concept_group(session, _spec(parents=(100,)))
    assert group.ids == frozenset({100, 101, 102})


def test_excluded_parents_subtract_their_descendants(session):
    _ancestry(session, [(200, 200), (200, 201), (200, 202), (202, 202)])
    spec = _spec(name="excl", parents=(200,), excluded=(202,))
    group = build_concept_group(session, spec)
    assert group.ids == frozenset({200, 201})


def test_exact_ids_are_included_without_expansion(session):
    """Exact members join the set directly, needing no ancestry at all."""
    group = build_concept_group(
        session, _spec(name="exact", parents=(), exact=(999999,))
    )
    assert group.ids == frozenset({999999})


def test_exact_ids_survive_exclusion(session):
    """Exclusions subtract descendants; exact members are explicit inclusions.

    Ordering matters: applying exclusion after the union would drop an exact
    member that also descends from an excluded anchor.
    """
    _ancestry(session, [(300, 300), (300, 301), (301, 301)])
    spec = _spec(name="exact_vs_excl", parents=(300,), excluded=(301,), exact=(301,))
    group = build_concept_group(session, spec)
    assert 301 in group


def test_include_descendants_false_uses_anchors_only(session):
    _ancestry(session, [(400, 400), (400, 401)])
    group = build_concept_group(
        session, _spec(name="anchors", parents=(400,), include_descendants=False)
    )
    assert group.ids == frozenset({400})


def test_membership_rejects_none(session):
    group = build_concept_group(session, _spec(name="none", parents=(), exact=(1,)))
    assert None not in group


# ── the two access paths agree ──────────────────────────────────────────────

def test_python_and_sql_paths_agree(session):
    """The instance and expression forms must select the same concepts.

    They are two renderings of one spec; this is the test that stops them
    drifting apart, which is the whole reason they share a definition.
    """
    _ancestry(session, [(500, 500), (500, 501), (500, 502)])
    spec = _spec(name="agreement", parents=(500,))
    group = build_concept_group(session, spec)

    candidates = [500, 501, 502, 503]
    via_sql = set(
        session.execute(
            sa.select(ANC.c.descendant_concept_id)
            .where(spec.expression_for(ANC.c.descendant_concept_id))
            .distinct()
        )
        .scalars()
        .all()
    )
    via_python = {cid for cid in candidates if cid in group}
    assert via_sql == via_python == {500, 501, 502}


def test_expression_needs_no_session():
    """The SQL form is reachable without a session — that is what lets a
    hybrid_property expose it at class level."""
    from omop_alchemy.cdm.model.vocabulary import Concept

    expr = _spec(parents=(1, 2)).expression_for(Concept.concept_id)
    assert isinstance(str(expr.compile()), str)


def test_empty_group_expression_is_false():
    from omop_alchemy.cdm.model.vocabulary import Concept

    expr = _spec(name="empty", parents=()).expression_for(Concept.concept_id)
    assert "false" in str(expr.compile()).lower()


# ── caching ─────────────────────────────────────────────────────────────────

def test_expansion_is_cached_per_vocabulary(session):
    """A second request must not rebuild."""
    spec = _spec(name="cached", parents=(), exact=(1,))
    assert resolve_concept_group(session, spec) is resolve_concept_group(session, spec)


def test_registered_identity_is_shared_across_engines(session):
    """Two engines under one vocabulary identity share expansions.

    This is the whole point of identity-scoped caching: recreating an engine
    against the same database must not re-run the closure.
    """
    engine = session.get_bind().engine
    register_vocabulary_identity(engine, "test-vocab-identity")
    try:
        registry_a = concept_group_registry(session)
        with so.Session(engine) as other_session:
            register_vocabulary_identity(other_session.get_bind().engine, "test-vocab-identity")
            registry_b = concept_group_registry(other_session)
        assert registry_a is registry_b
    finally:
        clear_concept_group_cache()


def test_unregistered_engines_do_not_share(session, fresh_engine):
    """Without an identity, each engine is its own scope.

    In-memory SQLite lands here deliberately: identical configuration, separate
    databases, so sharing would serve one database's concept sets for another.
    """
    with so.Session(fresh_engine) as other_session:
        assert concept_group_registry(session) is not concept_group_registry(
            other_session
        )


def test_connection_bound_sessions_share_their_engine_scope(session):
    """Session.get_bind() returns a Connection for connection-bound sessions.

    Without normalising to the engine, every such session would be its own cache
    scope and the cache would do nothing on that path — which is a pattern the
    fixtures themselves use.
    """
    engine = session.get_bind().engine
    with engine.connect() as connection:
        with so.Session(bind=connection) as conn_session:
            assert concept_group_registry(conn_session) is concept_group_registry(session)


# ── bounded cache and its observability ─────────────────────────────────────

def test_eviction_is_bounded_and_counted(session):
    """A too-small bound must evict, and a rebuild after eviction must be counted.

    rebuilds_after_evict is the signal that the shared budget is too small; it is
    otherwise invisible because it presents as ordinary slowness.
    """
    engine = session.get_bind().engine
    registry = ConceptGroupRegistry(engine, max_bytes=1)

    # exact members need no ancestry, so this exercises the bound, not the vocabulary
    for name in ("a", "b"):
        registry.register_spec(_spec(name=name, parents=(), exact=tuple(range(50))))

    registry.get("a")
    registry.get("b")
    assert registry.stats.evictions >= 1
    assert registry.stats.rebuilds_after_evict == 0

    registry.get("a")  # evicted above, so this is a rebuild
    assert registry.stats.rebuilds_after_evict == 1


def test_generous_bound_never_evicts(session):
    registry = ConceptGroupRegistry(session.get_bind().engine)
    for name in ("a", "b", "c"):
        registry.register_spec(_spec(name=name, parents=(), exact=(1, 2, 3)))
        registry.get(name)
    assert registry.stats.evictions == 0
    assert registry.stats.entries == 3


def test_cache_stats_are_reportable(session):
    resolve_concept_group(session, _spec(name="stats", parents=(), exact=(1,)))
    stats = concept_group_cache_stats()
    assert stats
    assert all("rebuilds_after_evict" in v for v in stats.values())


# ── governed specs use the non-deprecated 0.6.0 surface ─────────────────────

def test_governed_specs_emit_no_deprecation_warnings():
    """The oncology specs must read 0.6.0's role-specific accessors.

    Guards against slipping back to group-backed `.ids`, which 0.6.0 deprecates
    because it does not say whether members expand through descendants.
    """
    pytest.importorskip("omop_semantics")
    from omop_alchemy.toolkit.analytics.oncology import (
        CANCER_INDICATING_SURGERY,
        DIAGNOSTIC_STAGING_PROCEDURES,
        RADIOTHERAPY_PROCEDURES,
        SACT_DRUGS,
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for spec in (
            RADIOTHERAPY_PROCEDURES,
            CANCER_INDICATING_SURGERY,
            DIAGNOSTIC_STAGING_PROCEDURES,
            SACT_DRUGS,
        ):
            spec.parent_ids()
            spec.excluded_parent_ids()
            spec.exact_ids()

    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert not deprecations, [str(w.message) for w in deprecations]


def test_radiotherapy_spec_matches_governed_group():
    """The RT definition must come from omop-semantics, not be rebuilt locally."""
    pytest.importorskip("omop_semantics")
    from omop_semantics.runtime.default_valuesets import runtime

    from omop_alchemy.toolkit.analytics.oncology import RADIOTHERAPY_PROCEDURES

    assert set(RADIOTHERAPY_PROCEDURES.parent_ids()) == set(
        runtime.cancer_procedures.radiotherapy.parent_ids
    )


# ── standardness flags ──────────────────────────────────────────────────────
#
# ConceptGroupSpec shares its standardness vocabulary with LookupSpec and
# ConceptFilter: require_standard / include_classification. These pin that the
# names mean the same thing here as everywhere else in the package.

def _rendered(spec) -> str:
    """Compile the group's membership predicate to inspectable SQL."""
    column = sa.column("concept_id")
    return str(
        spec.expression_for(column).compile(
            compile_kwargs={"literal_binds": True}
        )
    ).lower()


def test_require_standard_defaults_off_and_adds_no_join():
    """Historical oncology behaviour: read concept_ancestor unfiltered."""
    spec = _spec()

    assert spec.require_standard is False
    assert "join" not in _rendered(spec)


def test_require_standard_joins_concept_and_filters_on_the_flag():
    spec = _spec(require_standard=True)
    rendered = _rendered(spec)

    assert "join" in rendered
    assert "'s'" in rendered


def test_include_classification_defaults_on_and_widens_the_predicate():
    """A group anchored on a classification node must not silently lose it."""
    spec = _spec(require_standard=True)

    assert spec.include_classification is True
    rendered = _rendered(spec)
    assert "'s'" in rendered and "'c'" in rendered
    assert " or " in rendered


def test_include_classification_off_narrows_to_standard_only():
    rendered = _rendered(_spec(require_standard=True, include_classification=False))

    assert "'s'" in rendered
    assert "'c'" not in rendered


def test_include_classification_is_inert_without_require_standard():
    """It widens require_standard; alone it constrains nothing."""
    assert _rendered(_spec(include_classification=True)) == _rendered(_spec())
