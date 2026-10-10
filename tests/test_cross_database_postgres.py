"""Reads across a split deployment: routed sessions, key filtering, split bootstrap.

Every test runs against two genuinely separate PostgreSQL databases, since
the behaviour under test is what happens when one statement cannot reach
both sides.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Iterator

import pytest
import sqlalchemy as sa
from oa_configurator import CrossDatabaseStatementError, Role

from omop_alchemy.cdm.base import ConceptValidationMixin

from omop_alchemy.cdm.model.clinical import Condition_Occurrence, Person
from omop_alchemy.cdm.model.clinical.condition_occurrence import Condition_OccurrenceView
from omop_alchemy.cdm.model.vocabulary import Concept, Concept_Ancestor
from omop_alchemy.cross_database import cdm_sessionmaker, filter_by_keys
from omop_alchemy.toolkit.core.concepts import RuntimeConceptSetSpec, runtime_concept_predicate

from .conftest import SplitCDM

pytestmark = [pytest.mark.postgresql, pytest.mark.db_dialect]

CONDITION_CONCEPT_ID = 201826
TYPE_CONCEPT_ID = 32817


@pytest.fixture
def statements(pg_split: SplitCDM) -> Iterator[Counter[str]]:
    """Statements executed per engine role, counted from the cursor level."""
    counts: Counter[str] = Counter()
    listeners = []
    for role, engine in (("primary", pg_split.primary), ("vocab", pg_split.vocab)):
        def _count(*_args, role=role, **_kwargs) -> None:
            counts[role] += 1

        sa.event.listen(engine, "before_cursor_execute", _count)
        listeners.append((engine, _count))
    yield counts
    for engine, listener in listeners:
        sa.event.remove(engine, "before_cursor_execute", listener)


def _seed_conditions(
    pg_split: SplitCDM, count: int, *, concept_ids: tuple[int, ...] = (CONDITION_CONCEPT_ID,)
) -> None:
    sessions = cdm_sessionmaker(pg_split.resolved, primary=pg_split.primary, vocab=pg_split.vocab)
    with sessions() as session:
        session.add(
            Person(
                person_id=1, year_of_birth=1980, gender_concept_id=8507,
                race_concept_id=8527, ethnicity_concept_id=38003564,
            )
        )
        session.flush()
        session.add_all(
            Condition_Occurrence(
                condition_occurrence_id=index, person_id=1,
                condition_concept_id=concept_ids[(index - 1) % len(concept_ids)],
                condition_start_date=date(2020, 1, 1),
                condition_type_concept_id=TYPE_CONCEPT_ID,
            )
            for index in range(1, count + 1)
        )
        session.commit()


class TestSplitBootstrap:
    def test_clinical_tables_lose_only_their_vocabulary_foreign_keys(self, pg_split: SplitCDM):
        table = Condition_Occurrence.__table__
        declared = {fk.target_fullname.split(".")[0] for fk in table.foreign_keys}
        assert Role.VOCAB.value in declared and Role.PRIMARY.value in declared
        expected_kept = {
            fk.column.table.name
            for fk in table.foreign_keys
            if fk.target_fullname.split(".")[0] != Role.VOCAB.value
        }

        created = sa.inspect(pg_split.primary).get_foreign_keys(
            "condition_occurrence", schema="public"
        )
        assert {fk["referred_table"] for fk in created} == expected_kept

    def test_vocabulary_tables_live_only_on_the_vocabulary_database(self, pg_split: SplitCDM):
        assert sa.inspect(pg_split.vocab).has_table("concept", schema="public")
        assert not sa.inspect(pg_split.primary).has_table("concept", schema="public")


class TestRoutedSession:
    def test_reference_resolves_from_the_vocabulary_database(
        self, pg_split: SplitCDM, statements: Counter[str]
    ):
        _seed_conditions(pg_split, 1)
        sessions = cdm_sessionmaker(pg_split.resolved, primary=pg_split.primary, vocab=pg_split.vocab)
        with sessions() as session:
            row = session.scalars(sa.select(Condition_OccurrenceView)).one()
            assert row.condition_concept.concept_name == "Type 2 diabetes mellitus"
            assert row.condition_type.concept_id == TYPE_CONCEPT_ID
            assert row.condition_source_concept is None

    def test_reference_loading_is_batched_by_relationship_not_by_row(
        self, pg_split: SplitCDM, statements: Counter[str]
    ):
        sessions = cdm_sessionmaker(pg_split.resolved, primary=pg_split.primary, vocab=pg_split.vocab)

        def vocab_queries_for_load() -> int:
            statements.clear()
            with sessions() as session:
                rows = session.scalars(sa.select(Condition_OccurrenceView)).all()
                assert {row.condition_concept.concept_id for row in rows} == {CONDITION_CONCEPT_ID}
            return statements["vocab"]

        _seed_conditions(pg_split, 1)
        one_row = vocab_queries_for_load()
        with pg_split.primary.begin() as connection:
            connection.execute(sa.delete(Condition_Occurrence.__table__))
            connection.execute(sa.delete(Person.__table__))
        _seed_conditions(pg_split, 25)
        assert vocab_queries_for_load() == one_row
        assert one_row > 0

    def test_join_across_the_boundary_is_refused(self, pg_split: SplitCDM):
        sessions = cdm_sessionmaker(pg_split.resolved, primary=pg_split.primary, vocab=pg_split.vocab)
        with sessions() as session, pytest.raises(CrossDatabaseStatementError):
            session.execute(
                sa.select(Condition_Occurrence.condition_occurrence_id).join(
                    Concept, Concept.concept_id == Condition_Occurrence.condition_concept_id
                )
            )

    def test_statement_naming_no_table_is_refused(self, pg_split: SplitCDM):
        sessions = cdm_sessionmaker(pg_split.resolved, primary=pg_split.primary, vocab=pg_split.vocab)
        with sessions() as session, pytest.raises(sa.exc.UnboundExecutionError):
            session.execute(sa.text("SELECT 1"))

    def test_swapped_engines_are_rejected(self, pg_split: SplitCDM):
        with pytest.raises(ValueError, match="create_engines"):
            cdm_sessionmaker(pg_split.resolved, primary=pg_split.vocab, vocab=pg_split.primary)


class TestFilterByKeys:
    def test_filters_clinical_rows_by_vocabulary_keys_across_databases(self, pg_split: SplitCDM):
        _seed_conditions(pg_split, 3)
        sessions = cdm_sessionmaker(pg_split.resolved, primary=pg_split.primary, vocab=pg_split.vocab)
        with sessions() as session:
            predicate = filter_by_keys(
                Condition_Occurrence.condition_concept_id,
                keys_select=sa.select(Concept.concept_id).where(Concept.domain_id == "Condition"),
                session=session,
            )
            assert "SELECT" not in str(predicate)
            ids = session.scalars(
                sa.select(Condition_Occurrence.condition_occurrence_id).where(predicate)
            ).all()
        assert sorted(ids) == [1, 2, 3]

    def test_no_matching_keys_matches_no_rows(self, pg_split: SplitCDM):
        _seed_conditions(pg_split, 1)
        sessions = cdm_sessionmaker(pg_split.resolved, primary=pg_split.primary, vocab=pg_split.vocab)
        with sessions() as session:
            predicate = filter_by_keys(
                Condition_Occurrence.condition_concept_id,
                keys_select=sa.select(Concept.concept_id).where(Concept.domain_id == "Nope"),
                session=session,
            )
            assert session.scalars(
                sa.select(Condition_Occurrence.condition_occurrence_id).where(predicate)
            ).all() == []


MISSING_CONCEPT_ID = 999_999


class _ConditionValidation(ConceptValidationMixin):
    """Validation over condition_occurrence's condition_concept_id only."""

    @classmethod
    def get_queryable_table(cls, session):
        return Condition_Occurrence.__table__

    @classmethod
    def concept_id_columns(cls):
        return {"condition_concept_id": Condition_Occurrence.__table__.c.condition_concept_id}


def test_concept_validation_reports_violations_across_databases(pg_split: SplitCDM):
    _seed_conditions(pg_split, 2, concept_ids=(CONDITION_CONCEPT_ID, MISSING_CONCEPT_ID))
    sessions = cdm_sessionmaker(pg_split.resolved, primary=pg_split.primary, vocab=pg_split.vocab)
    with sessions() as session:
        assert _ConditionValidation.referenced_concept_violations(session) == {
            "condition_concept_id": {MISSING_CONCEPT_ID}
        }
        assert _ConditionValidation.referenced_concept_violations(
            session, domain_id="Drug"
        ) == {"condition_concept_id": {CONDITION_CONCEPT_ID, MISSING_CONCEPT_ID}}


def test_concept_set_membership_across_databases_needs_the_session(pg_split: SplitCDM):
    _seed_conditions(pg_split, 2, concept_ids=(CONDITION_CONCEPT_ID, MISSING_CONCEPT_ID))
    with pg_split.vocab.begin() as connection:
        connection.execute(
            sa.insert(Concept_Ancestor.__table__).values(
                ancestor_concept_id=CONDITION_CONCEPT_ID,
                descendant_concept_id=CONDITION_CONCEPT_ID,
                min_levels_of_separation=0,
                max_levels_of_separation=0,
            )
        )
    spec = RuntimeConceptSetSpec(include_ancestor_ids=(CONDITION_CONCEPT_ID,))
    column = Condition_Occurrence.condition_concept_id
    sessions = cdm_sessionmaker(pg_split.resolved, primary=pg_split.primary, vocab=pg_split.vocab)
    with sessions() as session:
        ids = session.scalars(
            sa.select(Condition_Occurrence.condition_occurrence_id).where(
                runtime_concept_predicate(column, spec, session=session)
            )
        ).all()
        assert ids == [1]
        with pytest.raises(CrossDatabaseStatementError):
            session.execute(
                sa.select(Condition_Occurrence.condition_occurrence_id).where(
                    runtime_concept_predicate(column, spec)
                )
            )
