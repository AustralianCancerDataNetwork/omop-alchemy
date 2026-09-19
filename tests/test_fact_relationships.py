from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite

import omop_alchemy.toolkit.core.facts.registry as registry_module
from omop_alchemy.cdm.model import Condition_Occurrence, Procedure_Occurrence
from omop_alchemy.toolkit.core.events import ClinicalEventIdentity
from omop_alchemy.toolkit.core.facts import (
    CONDITION_DOMAIN_CONCEPT_ID,
    FactIdentity,
    FactRelationshipDiagnostic,
    FactRelationshipDiagnosticCode,
    FactRelationshipKind,
    UnsupportedFactDomainError,
    canonical_fact_projection,
    canonical_fact_relationship_values,
    clinical_event_identity,
    condition_etiology_registry,
    fact_domain_spec,
    fact_identity_from_event,
    fact_relationship_queries,
    fact_source,
)


@pytest.fixture
def registry():
    return condition_etiology_registry(
        primary_etiology_of=9001,
        has_primary_etiology=9002,
        contributing_etiology_of=9003,
        has_contributing_etiology=9004,
        non_contributing_to=9005,
        has_non_contributing_condition=9006,
    )


def _fact_rows(*rows: tuple[int, int]) -> sa.CompoundSelect:
    projections = []
    for event_id, person_id in rows:
        projections.append(
            sa.select(
                sa.literal(CONDITION_DOMAIN_CONCEPT_ID).label("domain_concept_id"),
                sa.literal(person_id).label("person_id"),
                sa.literal(event_id).label("event_id"),
                sa.literal(date(2026, 1, event_id % 28 + 1), type_=sa.Date()).label(
                    "event_date"
                ),
                sa.cast(sa.null(), sa.DateTime()).label("event_datetime"),
                sa.literal(100_000 + event_id).label("event_concept_id"),
                sa.literal(1147127).label("event_field_concept_id"),
                sa.literal("condition_occurrence").label("event_source_table"),
            )
        )
    return sa.union_all(*projections)


def _relationship_rows(
    *rows: tuple[int, int, int],
) -> sa.CompoundSelect:
    projections = []
    for from_id, to_id, relationship_id in rows:
        projections.append(
            sa.select(
                sa.literal(CONDITION_DOMAIN_CONCEPT_ID).label("domain_concept_id_1"),
                sa.literal(from_id).label("fact_id_1"),
                sa.literal(CONDITION_DOMAIN_CONCEPT_ID).label("domain_concept_id_2"),
                sa.literal(to_id).label("fact_id_2"),
                sa.literal(relationship_id).label("relationship_concept_id"),
            )
        )
    return sa.union_all(*projections)


def _scoped_facts(facts: sa.CompoundSelect, *event_ids: int) -> sa.Select:
    rows = facts.subquery()
    return sa.select(*rows.c).where(rows.c.event_id.in_(event_ids))


def test_fact_identity_includes_domain_and_converts_to_event_identity():
    assert FactIdentity(19, 7) != FactIdentity(10, 7)
    event = ClinicalEventIdentity("condition_occurrence", 7)

    assert fact_identity_from_event(event, "Condition") == FactIdentity(19, 7)
    assert clinical_event_identity(FactIdentity(19, 7)) == event


def test_condition_is_the_only_initial_fact_domain():
    assert fact_domain_spec(Condition_Occurrence).domain_concept_id == 19
    assert (
        "domain_concept_id"
        in canonical_fact_projection(Condition_Occurrence).selected_columns.keys()
    )

    with pytest.raises(UnsupportedFactDomainError, match="Condition Domain"):
        fact_domain_spec(Procedure_Occurrence)


def test_registry_is_inverse_complete_immutable_and_writes_forward_only(registry):
    primary = registry.resolve(FactRelationshipKind.primary_etiology)
    forward = registry.concepts_by_id[primary.relationship_concept_id]
    assert (
        registry.concepts_by_id[forward.inverse_concept_id].inverse_concept_id == 9001
    )

    with pytest.raises(TypeError):
        registry.concepts_by_id[9999] = forward

    values = canonical_fact_relationship_values(
        FactIdentity(19, 1),
        FactIdentity(19, 10),
        relationship=FactRelationshipKind.primary_etiology,
        registry=registry,
    )
    assert values == {
        "domain_concept_id_1": 19,
        "fact_id_1": 1,
        "domain_concept_id_2": 19,
        "fact_id_2": 10,
        "relationship_concept_id": 9001,
    }


def test_default_registry_uses_the_governed_semantics_hierarchy(monkeypatch):
    values = SimpleNamespace(
        primary_etiology_of=SimpleNamespace(concept_id=9001),
        has_primary_etiology=SimpleNamespace(concept_id=9002),
        contributing_etiology_of=SimpleNamespace(concept_id=9003),
        has_contributing_etiology=SimpleNamespace(concept_id=9004),
        non_contributing_to=SimpleNamespace(concept_id=9005),
        has_non_contributing_condition=SimpleNamespace(concept_id=9006),
    )
    runtime = SimpleNamespace(
        fact_relationships=SimpleNamespace(relationship_types=values)
    )
    monkeypatch.setattr(registry_module, "default_semantics_runtime", lambda: runtime)
    registry_module.default_fact_relationship_registry.cache_clear()

    result = registry_module.default_fact_relationship_registry()

    assert result.resolve("primary_etiology").relationship_concept_id == 9001
    registry_module.default_fact_relationship_registry.cache_clear()


@pytest.mark.parametrize("dialect", [sqlite.dialect(), postgresql.dialect()])
def test_query_contract_compiles_for_supported_dialects(registry, dialect):
    facts = _fact_rows((1, 101), (10, 101))
    relationships = _relationship_rows((1, 10, 9001))
    queries = fact_relationship_queries(
        fact_source(facts, domain="Condition"),
        fact_source(facts, domain="Condition"),
        relationship=FactRelationshipKind.primary_etiology,
        registry=registry,
        relationships=relationships,
        diagnostics=True,
    )

    assert "relationship_concept_id" in str(queries.matches.compile(dialect=dialect))
    assert queries.diagnostics is not None
    assert "person_mismatch" in str(
        queries.diagnostics.compile(
            dialect=dialect, compile_kwargs={"literal_binds": True}
        )
    )


def test_sqlite_resolves_outbound_and_inbound_without_direction_flag(registry):
    engine = sa.create_engine("sqlite://")
    facts = _fact_rows((1, 101), (2, 101), (3, 101), (10, 101))
    relationships = _relationship_rows(
        (1, 10, 9001),
        (2, 10, 9003),
        (3, 10, 9005),
    )
    outbound = fact_relationship_queries(
        fact_source(_scoped_facts(facts, 1), domain=19),
        fact_source(facts, domain=19),
        relationship="primary_etiology",
        registry=registry,
        relationships=relationships,
    )
    inbound = fact_relationship_queries(
        fact_source(facts, domain=19),
        fact_source(_scoped_facts(facts, 10), domain=19),
        relationship="contributing_etiology",
        registry=registry,
        relationships=relationships,
    )

    with engine.connect() as connection:
        outbound_row = connection.execute(outbound.matches).mappings().one()
        inbound_row = connection.execute(inbound.matches).mappings().one()
    assert (outbound_row["from_fact_id"], outbound_row["to_fact_id"]) == (1, 10)
    assert (inbound_row["from_fact_id"], inbound_row["to_fact_id"]) == (2, 10)


def test_diagnostics_distinguish_missing_endpoints_and_person_mismatch(registry):
    engine = sa.create_engine("sqlite://")
    facts = _fact_rows((1, 101), (4, 202), (10, 101))
    relationships = _relationship_rows(
        (1, 10, 9001),
        (4, 10, 9001),
        (99, 10, 9001),
        (1, 88, 9001),
    )
    queries = fact_relationship_queries(
        fact_source(facts, domain=19, complete=True),
        fact_source(facts, domain=19, complete=True),
        relationship="primary_etiology",
        registry=registry,
        relationships=relationships,
        diagnostics=True,
    )

    with engine.connect() as connection:
        matches = connection.execute(queries.matches).mappings().all()
        diagnostics_query = queries.diagnostics
        assert diagnostics_query is not None
        diagnostics = [
            FactRelationshipDiagnostic.from_mapping(row)
            for row in connection.execute(diagnostics_query).mappings()
        ]

    assert [(row["from_fact_id"], row["to_fact_id"]) for row in matches] == [(1, 10)]
    assert {diagnostic.diagnostic_code for diagnostic in diagnostics} == {
        FactRelationshipDiagnosticCode.missing_from_fact,
        FactRelationshipDiagnosticCode.missing_to_fact,
        FactRelationshipDiagnosticCode.person_mismatch,
    }


def test_filtered_selectables_do_not_claim_missing_endpoints(registry):
    engine = sa.create_engine("sqlite://")
    facts = _fact_rows((1, 101), (10, 101))
    relationships = _relationship_rows((99, 10, 9001))
    queries = fact_relationship_queries(
        fact_source(facts, domain=19),
        fact_source(facts, domain=19),
        relationship="primary_etiology",
        registry=registry,
        relationships=relationships,
        diagnostics=True,
    )

    with engine.connect() as connection:
        diagnostics_query = queries.diagnostics
        assert diagnostics_query is not None
        assert connection.execute(diagnostics_query).all() == []


@pytest.mark.requires_database("test_cdm_db")
def test_postgresql_executes_fact_relationship_contract(pg_session, registry):
    facts = _fact_rows((1, 101), (10, 101))
    relationships = _relationship_rows((1, 10, 9001))
    queries = fact_relationship_queries(
        fact_source(facts, domain=19),
        fact_source(facts, domain=19),
        relationship="primary_etiology",
        registry=registry,
        relationships=relationships,
    )

    row = pg_session.execute(queries.matches).mappings().one()
    assert (row["from_fact_id"], row["to_fact_id"]) == (1, 10)
