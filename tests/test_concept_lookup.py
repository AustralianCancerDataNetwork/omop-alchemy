"""Lookup construction over OMOP vocabulary concepts."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from omop_alchemy.cdm.model.vocabulary import Concept, Concept_Synonym
from omop_alchemy.cdm.query import ConceptFilter
from omop_alchemy.toolkit.core.concepts import LookupSpec, OMOPConceptSource


def _concept(
    concept_id: int,
    *,
    concept_name: str,
    domain_id: str = "Condition",
    vocabulary_id: str = "SNOMED",
    standard_concept: str | None = "S",
    invalid_reason: str | None = None,
) -> Concept:
    return Concept(
        concept_id=concept_id,
        concept_name=concept_name,
        domain_id=domain_id,
        vocabulary_id=vocabulary_id,
        concept_class_id="Clinical Finding",
        standard_concept=standard_concept,
        concept_code=f"C{concept_id}",
        valid_start_date=date(1970, 1, 1),
        valid_end_date=date(2099, 12, 31),
        invalid_reason=invalid_reason,
    )


def test_lookup_standard_and_active_use_shared_predicates(session):
    session.add_all(
        [
            _concept(910001, concept_name="Classification concept", standard_concept=" C "),
            _concept(910002, concept_name="Deprecated concept", invalid_reason="D"),
            _concept(910003, concept_name="Blank standard concept", standard_concept=" "),
        ]
    )
    session.flush()

    default_index = OMOPConceptSource.build_lookup(
        session,
        LookupSpec(
            name="default",
            domain_id="Condition",
            include=("concept_name",),
        ),
    )
    active_only_index = OMOPConceptSource.build_lookup(
        session,
        LookupSpec(
            name="active_only",
            domain_id="Condition",
            require_active=True,
            include=("concept_name",),
        ),
    )

    # Recognition is permissive by default. A classification concept and a
    # deprecated concept both resolve, so the caller keeps the hit and can
    # follow "Maps to" / "Concept replaced by" to a valid target. Filtering
    # either out here would discard the term with nothing to resolve forward.
    assert default_index.lookup("classification concept") == 910001
    assert default_index.lookup("deprecated concept") == 910002

    # Standardness is still normalised: a blank flag is not standard, so it is
    # excluded by require_standard regardless of the permissive validity default.
    assert default_index.lookup("blank standard concept") == 0

    # Selection-shaped callers opt in to the strict rule.
    assert active_only_index.lookup("deprecated concept") == 0
    assert active_only_index.lookup("classification concept") == 910001


def test_synonym_lookup_is_scoped_to_indexed_concepts(session):
    session.add_all(
        [
            _concept(920001, concept_name="Scoped concept"),
            _concept(920002, concept_name="Outside concept", domain_id="Race"),
            Concept_Synonym(
                concept_id=920001,
                concept_synonym_name="Scoped synonym",
                language_concept_id=0,
            ),
            Concept_Synonym(
                concept_id=920002,
                concept_synonym_name="Outside synonym",
                language_concept_id=0,
            ),
        ]
    )
    session.flush()

    statements: list[str] = []

    def capture_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "concept_synonym" in statement.lower():
            statements.append(statement.lower())

    engine = session.get_bind().engine
    sa.event.listen(engine, "before_cursor_execute", capture_sql)
    try:
        index = OMOPConceptSource.build_lookup(
            session,
            LookupSpec(
                name="synonyms",
                domain_id="Condition",
                include=("concept_name",),
                include_synonyms=True,
            ),
        )
    finally:
        sa.event.remove(engine, "before_cursor_execute", capture_sql)

    assert index.lookup("scoped synonym") == 920001
    assert index.lookup("outside synonym") == 0
    assert statements
    assert any(" join " in statement for statement in statements)


def _fetch(session, **kwargs) -> set[int]:
    return {
        row.concept_id
        for row in OMOPConceptSource.fetch_concepts(
            session,
            domain_id="Condition",
            vocabulary_id=("SNOMED",),
            require_standard=True,
            **kwargs,
        )
    }


def test_fetch_concepts_delegates_shared_predicates_to_concept_filter(session):
    """Both standardness and validity flags must reach ConceptFilter unchanged.

    Seeds a concept for each flag state so the comparison is not four copies of
    the same all-standard, all-active fixture set.
    """
    session.add_all(
        [
            _concept(950001, concept_name="Strict standard"),
            _concept(950002, concept_name="Classification", standard_concept="C"),
            _concept(950003, concept_name="Deprecated", invalid_reason="D"),
            _concept(950004, concept_name="Padded standard", standard_concept=" S "),
        ]
    )
    session.flush()

    for include_classification in (False, True):
        for require_active in (False, True):
            filtered = set(
                session.execute(
                    ConceptFilter(
                        domains=("Condition",),
                        vocabularies=("SNOMED",),
                        require_standard=True,
                        include_classification=include_classification,
                        require_active=require_active,
                    ).apply(sa.select(Concept.concept_id))
                )
                .scalars()
                .all()
            )
            fetched = _fetch(
                session,
                include_classification=include_classification,
                require_active=require_active,
            )
            assert fetched == filtered, (include_classification, require_active)

    permissive = _fetch(session, include_classification=True, require_active=False)
    strict = _fetch(session, include_classification=False, require_active=True)

    # The flags genuinely move rows, so the equalities above are not vacuous.
    assert 950002 in permissive and 950002 not in strict  # classification
    assert 950003 in permissive and 950003 not in strict  # deprecated
    # A whitespace-padded 'S' is standard under both — the normalisation the old
    # hand-rolled ``standard_concept == "S"`` comparison got wrong.
    assert 950004 in permissive and 950004 in strict


def test_fetch_concepts_defaults_are_permissive(session):
    """Recognition-shaped defaults: classification in, validity unfiltered."""
    session.add_all(
        [
            _concept(960001, concept_name="Default classification", standard_concept="C"),
            _concept(960002, concept_name="Default deprecated", invalid_reason="D"),
        ]
    )
    session.flush()

    defaults = _fetch(session)

    assert 960001 in defaults
    assert 960002 in defaults
