"""Tests for ConceptFilter.apply(), the shared CDM concept-table WHERE/LIMIT builder."""

from datetime import date

import pytest
import sqlalchemy as sa

from omop_alchemy.cdm.model import (
    InvalidReasonFlag,
    StandardConceptFlag,
    normalised_flag,
    normalised_flag_expr,
)
from omop_alchemy.cdm.model.vocabulary import (
    Concept,
    Concept_Relationship,
    ConceptView,
    Drug_Strength,
    Relationship,
    Source_To_Concept_Map,
)
from omop_alchemy.cdm.query import ConceptFilter


def _seed_flagged_concept(session, concept_id, *, standard_concept=None, invalid_reason=None):
    """Add one concept carrying an arbitrary — possibly dirty — flag value."""
    session.add(
        Concept(
            concept_id=concept_id,
            concept_name=f"flag fixture {concept_id}",
            domain_id="Condition",
            vocabulary_id="SNOMED",
            concept_class_id="Clinical Finding",
            standard_concept=standard_concept,
            concept_code=f"flag-{concept_id}",
            valid_start_date=date(1970, 1, 1),
            valid_end_date=date(2099, 12, 31),
            invalid_reason=invalid_reason,
        )
    )
    session.flush()


class TestConceptFilterApply:
    def test_empty_filter_adds_no_clauses(self):
        query = sa.select(Concept.concept_id)
        result = ConceptFilter().apply(query)

        assert str(result) == str(query)

    def test_concept_ids_adds_in_clause(self):
        query = sa.select(Concept.concept_id)
        result = ConceptFilter(concept_ids=(1, 2, 3)).apply(query)

        compiled = str(result)
        assert "WHERE" in compiled
        assert "concept_id IN" in compiled

    def test_domains_adds_in_clause(self):
        query = sa.select(Concept.concept_id)
        result = ConceptFilter(domains=("Condition", "Drug")).apply(query)

        assert "domain_id IN" in str(result)

    def test_vocabularies_adds_in_clause(self):
        query = sa.select(Concept.concept_id)
        result = ConceptFilter(vocabularies=("SNOMED",)).apply(query)

        assert "vocabulary_id IN" in str(result)

    def test_require_standard_adds_clause(self):
        query = sa.select(Concept.concept_id)
        result = ConceptFilter(require_standard=True).apply(query)

        compiled = str(result).lower()
        assert "nullif" in compiled
        assert "trim" in compiled
        assert "coalesce" in compiled

    def test_require_active_adds_clause(self):
        query = sa.select(Concept.concept_id)
        result = ConceptFilter(require_active=True).apply(query)

        compiled = str(result).lower()
        assert "nullif" in compiled
        assert "trim" in compiled
        assert "is null" in compiled

    def test_require_active_does_not_exclude_null_invalid_reason(self, session):
        """Regression test: NULL invalid_reason (the normal, active case) must
        not be dropped by a SQL NOT IN three-valued-logic bug."""
        query = sa.select(Concept.concept_id)
        result = ConceptFilter(require_active=True).apply(query)

        returned_ids = set(session.scalars(result).all())
        all_ids = set(session.scalars(sa.select(Concept.concept_id)).all())
        assert returned_ids == all_ids
        assert returned_ids  # sanity: fixtures aren't empty

    def test_require_standard_executes_and_matches_fixtures(self, session):
        """Regression test: prove require_standard actually executes and
        matches real 'S' rows, not just that the compiled clause looks right."""
        query = sa.select(Concept.concept_id)
        result = ConceptFilter(require_standard=True).apply(query)

        returned_ids = set(session.scalars(result).all())
        all_ids = set(session.scalars(sa.select(Concept.concept_id)).all())
        assert returned_ids == all_ids
        assert returned_ids  # sanity: fixtures aren't empty

    def test_require_standard_compiles_with_literal_binds(self):
        """The standard filter must compile to the standard flag only."""
        query = sa.select(Concept.concept_id)
        result = ConceptFilter(require_standard=True).apply(query)

        compiled = str(result.compile(compile_kwargs={"literal_binds": True}))
        assert "'S'" in compiled
        assert "'C'" not in compiled

    def test_include_classification_widens_to_the_union(self):
        """include_classification restores the pre-1.x 'S' or 'C' behaviour."""
        query = sa.select(Concept.concept_id)
        result = ConceptFilter(
            require_standard=True, include_classification=True
        ).apply(query)

        compiled = str(result.compile(compile_kwargs={"literal_binds": True}))
        assert "'S'" in compiled
        assert "'C'" in compiled
        assert " OR " in compiled.upper()

    def test_include_classification_is_inert_without_require_standard(self):
        """It widens require_standard; alone it imposes no constraint at all."""
        query = sa.select(Concept.concept_id)
        result = ConceptFilter(include_classification=True).apply(query)

        assert str(result) == str(query)

    def test_include_classification_matches_the_legacy_union(self, session):
        """The composed union must reproduce the old ``IN ('S', 'C')`` result set
        exactly, so callers can restore prior behaviour by opting in."""
        for offset, flag in enumerate(("S", "C", None, "", "   ", "X"), start=1):
            _seed_flagged_concept(session, 930000 + offset, standard_concept=flag)

        union_ids = set(
            session.scalars(
                ConceptFilter(
                    require_standard=True, include_classification=True
                ).apply(sa.select(Concept.concept_id))
            ).all()
        )
        legacy_ids = set(
            session.scalars(
                sa.select(Concept.concept_id).where(
                    normalised_flag_expr(Concept.standard_concept).in_(("S", "C"))
                )
            ).all()
        )

        assert union_ids == legacy_ids
        # Sanity: the fixture actually contains both flags, so this is not a
        # vacuous comparison of two empty sets.
        assert 930001 in union_ids and 930002 in union_ids

    def test_require_standard_alone_excludes_classification(self, session):
        """Default behaviour: 'C' is not a valid mapping target."""
        _seed_flagged_concept(session, 931001, standard_concept="S")
        _seed_flagged_concept(session, 931002, standard_concept="C")

        returned = set(
            session.scalars(
                ConceptFilter(require_standard=True).apply(
                    sa.select(Concept.concept_id)
                )
            ).all()
        )

        assert 931001 in returned
        assert 931002 not in returned

    def test_limit_is_applied(self):
        query = sa.select(Concept.concept_id)
        result = ConceptFilter(limit=5).apply(query)

        assert "LIMIT" in str(result)

    def test_negative_limit_raises(self):
        with pytest.raises(ValueError, match="positive integer"):
            ConceptFilter(limit=0)

    def test_is_empty(self):
        assert ConceptFilter().is_empty()
        assert not ConceptFilter(limit=5).is_empty()
        assert not ConceptFilter(domains=("Drug",)).is_empty()
        assert not ConceptFilter(require_standard=True).is_empty()
        assert not ConceptFilter(require_active=True).is_empty()
        # include_classification widens require_standard rather than
        # constraining on its own, so it must not make a filter non-empty.
        assert ConceptFilter(include_classification=True).is_empty()


class TestNormalisedFlagExpr:
    """normalised_flag_expr must trim whitespace and turn blank strings into
    NULL, while leaving NULL and non-blank values (canonical or not) alone."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            (None, None),
            ("", None),
            ("   ", None),
            ("S", "S"),
            (" S ", "S"),
            ("X", "X"),  # non-canonical, non-blank values pass through unchanged
        ],
    )
    def test_normalises_value(self, session, raw, expected):
        result = session.scalar(
            sa.select(normalised_flag_expr(sa.literal(raw, type_=sa.String)))
        )
        assert result == expected


class TestConceptViewFlags:
    """ConceptView flags must distinguish standard and classification concepts."""

    @pytest.mark.parametrize(
        "standard_concept, expected",
        [
            (StandardConceptFlag.STANDARD, True),
            (f" {StandardConceptFlag.STANDARD} ", True),
            (StandardConceptFlag.CLASSIFICATION, False),
            (f" {StandardConceptFlag.CLASSIFICATION} ", False),
            (None, False),
            ("", False),
            ("   ", False),
            ("X", False),
        ],
    )
    def test_is_standard(self, standard_concept, expected):
        cv = ConceptView(standard_concept=standard_concept)
        assert cv.is_standard is expected

    @pytest.mark.parametrize(
        "standard_concept, expected",
        [
            (StandardConceptFlag.CLASSIFICATION, True),
            (f" {StandardConceptFlag.CLASSIFICATION} ", True),
            (StandardConceptFlag.STANDARD, False),
            (f" {StandardConceptFlag.STANDARD} ", False),
            (None, False),
            ("", False),
            ("   ", False),
            ("X", False),
        ],
    )
    def test_is_classification(self, standard_concept, expected):
        cv = ConceptView(standard_concept=standard_concept)
        assert cv.is_classification is expected

    @pytest.mark.parametrize(
        "invalid_reason, expected",
        [
            (None, True),
            ("", True),
            ("   ", True),
            (InvalidReasonFlag.DELETED, False),
            (InvalidReasonFlag.UPDATED, False),
        ],
    )
    def test_is_valid(self, invalid_reason, expected):
        cv = ConceptView(invalid_reason=invalid_reason)
        assert cv.is_valid is expected

    @pytest.mark.parametrize(
        "invalid_reason", [None, "", "   ", "D", "U", "X", " X "]
    )
    def test_is_valid_agrees_with_require_active_filter(self, session, invalid_reason):
        """PR feedback regression test: a 'dirty' row must not be judged
        active by ConceptFilter(require_active=True) but invalid by
        ConceptView.is_valid, or vice versa."""
        included_by_filter = session.scalar(
            sa.select(normalised_flag_expr(sa.literal(invalid_reason, type_=sa.String)).is_(None))
        )
        cv = ConceptView(invalid_reason=invalid_reason)
        assert cv.is_valid == included_by_filter

    @pytest.mark.parametrize(
        "standard_concept", [None, "", "   ", "S", " S ", "C", " C ", "X", " X "]
    )
    def test_is_standard_agrees_with_require_standard_filter(self, session, standard_concept):
        """Same consistency check as above, for is_standard/require_standard."""
        included_by_filter = bool(
            session.scalar(
                sa.select(
                    sa.func.coalesce(
                        normalised_flag_expr(
                            sa.literal(standard_concept, type_=sa.String)
                        )
                        == StandardConceptFlag.STANDARD.value,
                        sa.false(),
                    )
                )
            )
        )
        cv = ConceptView(standard_concept=standard_concept)
        assert cv.is_standard == included_by_filter


class TestPredicateComplement:
    """The ``_expr`` predicates must be two-valued.

    ``nullif(trim(col), '') = 'S'`` evaluates to SQL NULL for an unset flag, so a
    bare ``NOT`` over it silently drops every unset-flag row instead of returning
    it. The predicates coalesce to false to close that hole; these tests pin it,
    because the failure is invisible in a plain WHERE clause.
    """

    FLAGS = ("S", "C", None, "", "   ", "X")

    def _seed_all_flags(self, session, base):
        for offset, flag in enumerate(self.FLAGS, start=1):
            _seed_flagged_concept(session, base + offset, standard_concept=flag)

    def _count(self, session, expr):
        return session.scalar(
            sa.select(sa.func.count()).select_from(Concept).where(expr)
        )

    def test_is_standard_negation_returns_the_complement(self, session):
        self._seed_all_flags(session, 940000)
        total = session.scalar(sa.select(sa.func.count()).select_from(Concept))

        matching = self._count(session, Concept.is_standard_expr())
        complement = self._count(session, sa.not_(Concept.is_standard_expr()))

        assert matching + complement == total
        assert complement > 0  # unset-flag rows must survive the negation

    def test_is_classification_negation_returns_the_complement(self, session):
        self._seed_all_flags(session, 941000)
        total = session.scalar(sa.select(sa.func.count()).select_from(Concept))

        matching = self._count(session, Concept.is_classification_expr())
        complement = self._count(session, sa.not_(Concept.is_classification_expr()))

        assert matching + complement == total
        assert matching > 0

    def test_standard_and_classification_are_disjoint(self, session):
        self._seed_all_flags(session, 942000)

        both = self._count(
            session,
            sa.and_(Concept.is_standard_expr(), Concept.is_classification_expr()),
        )
        assert both == 0

    @pytest.mark.parametrize("flag", FLAGS)
    def test_python_and_sql_agree(self, session, flag):
        """The property and the expression must never disagree for any input."""
        concept_id = 943000 + abs(hash(str(flag))) % 900
        _seed_flagged_concept(session, concept_id, standard_concept=flag)

        row = session.execute(
            sa.select(
                Concept.is_standard_expr().label("sql_standard"),
                Concept.is_classification_expr().label("sql_classification"),
            ).where(Concept.concept_id == concept_id)
        ).one()
        concept = session.get(Concept, concept_id)

        assert concept.is_standard is bool(row.sql_standard)
        assert concept.is_classification is bool(row.sql_classification)


class TestRelationshipFlagPredicates:
    """``relationship.is_hierarchical`` / ``defines_ancestry`` hold '1'/'0' as
    *text*. ``'0'`` is truthy in Python, so a bare ``bool()`` on the raw column
    reports every relationship as hierarchical — the defect these predicates
    exist to make unreachable.
    """

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("1", True),
            (" 1 ", True),
            ("0", False),
            (" 0 ", False),
            ("", False),
            ("   ", False),
            (None, False),
            ("X", False),
        ],
    )
    def test_is_hierarchical_relationship(self, raw, expected):
        rel = Relationship(is_hierarchical=raw)
        assert rel.is_hierarchical_relationship is expected

    @pytest.mark.parametrize(
        "raw, expected",
        [("1", True), (" 1 ", True), ("0", False), ("", False), (None, False)],
    )
    def test_is_ancestry_defining(self, raw, expected):
        rel = Relationship(defines_ancestry=raw)
        assert rel.is_ancestry_defining is expected

    def test_zero_string_is_not_truthy_through_the_predicate(self):
        """Guards the exact bug: bool('0') is True, the predicate must not be."""
        rel = Relationship(is_hierarchical="0", defines_ancestry="0")
        assert bool(rel.is_hierarchical) is True  # the raw column, as stored
        assert rel.is_hierarchical_relationship is False
        assert rel.is_ancestry_defining is False

    def test_expressions_compile_to_the_true_flag_only(self):
        for expr in (
            Relationship.is_hierarchical_relationship_expr(),
            Relationship.is_ancestry_defining_expr(),
        ):
            compiled = str(expr.compile(compile_kwargs={"literal_binds": True}))
            assert "'1'" in compiled
            assert "'0'" not in compiled
            assert "coalesce" in compiled.lower()

    def test_defines_ancestry_expr_discriminates_in_sql(self, session):
        """Fixtures carry one ancestry-defining and one non-defining relationship."""
        defining = set(
            session.scalars(
                sa.select(Relationship.relationship_id).where(
                    Relationship.is_ancestry_defining_expr()
                )
            ).all()
        )
        all_ids = set(session.scalars(sa.select(Relationship.relationship_id)).all())

        assert defining
        assert defining != all_ids  # the '0' row must be excluded

    def test_python_and_sql_agree(self, session):
        for rel in session.scalars(sa.select(Relationship)).all():
            sql_value = session.scalar(
                sa.select(Relationship.is_ancestry_defining_expr()).where(
                    Relationship.relationship_id == rel.relationship_id
                )
            )
            assert rel.is_ancestry_defining is bool(sql_value)


class TestInvalidReasonMixin:
    """``InvalidReasonMixin`` is the single implementation of the validity rule
    for every CDM table carrying ``invalid_reason``."""

    MIXED_IN = (Concept, Concept_Relationship, Drug_Strength, Source_To_Concept_Map)

    @pytest.mark.parametrize("model", MIXED_IN, ids=lambda m: m.__name__)
    @pytest.mark.parametrize(
        "raw, expected",
        [
            (None, True),
            ("", True),
            ("   ", True),
            (InvalidReasonFlag.DELETED, False),
            (InvalidReasonFlag.UPDATED, False),
            (" D ", False),
            ("X", False),
        ],
    )
    def test_is_valid_is_identical_across_tables(self, model, raw, expected):
        assert model(invalid_reason=raw).is_valid is expected

    @pytest.mark.parametrize("model", MIXED_IN, ids=lambda m: m.__name__)
    def test_is_valid_expr_targets_its_own_table(self, model):
        compiled = str(model.is_valid_expr()).lower()
        assert f"{model.__tablename__}.invalid_reason" in compiled
        assert "is null" in compiled

    @pytest.mark.parametrize("model", MIXED_IN, ids=lambda m: m.__name__)
    def test_python_and_sql_agree_on_blank(self, session, model):
        """A blank string is *not* SQL NULL — the case a raw ``IS NULL`` test
        gets wrong, and the reason the normalisation exists."""
        for raw in (None, "", "   ", "D"):
            sql_value = session.scalar(
                sa.select(
                    normalised_flag_expr(sa.literal(raw, type_=sa.String)).is_(None)
                )
            )
            assert model(invalid_reason=raw).is_valid is bool(sql_value)


class TestNormalisedFlagPairing:
    """``normalised_flag`` is the published Python counterpart to
    ``normalised_flag_expr``; the two must never diverge."""

    @pytest.mark.parametrize(
        "raw", [None, "", " ", "   ", "S", " S ", "C", "D", "1", "0", "X", " X "]
    )
    def test_python_matches_sql(self, session, raw):
        sql_value = session.scalar(
            sa.select(normalised_flag_expr(sa.literal(raw, type_=sa.String)))
        )
        assert normalised_flag(raw) == sql_value
