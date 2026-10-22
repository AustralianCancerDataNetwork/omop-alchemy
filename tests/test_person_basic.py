import sqlalchemy as sa
from sqlalchemy.orm import Session
from datetime import date
from omop_alchemy.cdm.model.clinical import Person, PersonView

from omop_alchemy.cdm.model.vocabulary import Concept


def test_person_table_exists(engine):
    """Test person table exists."""
    insp = sa.inspect(engine)
    assert "person" in insp.get_table_names()


def test_person_rows_exist(session: Session):
    """Test person rows exist."""
    count = session.scalar(sa.select(sa.func.count()).select_from(Person))
    if count:
        assert count > 0


def test_person_has_required_fields(session: Session):
    """Test person has required fields."""
    p = session.scalars(sa.select(Person).limit(1)).first()
    assert p is not None

    # Structural expectations
    assert p.person_id is not None
    assert p.year_of_birth is not None
    assert p.gender_concept_id is not None
    assert p.race_concept_id is not None
    assert p.ethnicity_concept_id is not None


def test_person_repr_is_stable(session: Session):
    """Test person repr is stable."""
    p = session.scalars(sa.select(Person).limit(1)).first()
    s = repr(p)

    assert s.startswith("<Person ")
    assert s.endswith(">")


def test_person_view_resolves_gender(session: Session):
    """Test person view resolves gender."""
    p = session.scalars(sa.select(PersonView).limit(1)).first()

    if p:
        assert p.gender is not None
        assert p.gender.concept_id == p.gender_concept_id
        assert p.gender.domain_id == "Gender"


def test_person_view_optional_relationships_safe(session: Session):
    """Test person view optional relationships safe."""
    p = session.scalars(sa.select(PersonView).limit(1)).first()

    if p:
        _ = p.location
        _ = p.provider
        _ = p.care_site


def test_person_view_repr_contains_semantics(session: Session):
    """Test person view repr contains semantics."""
    p = session.scalars(sa.select(PersonView).limit(1)).first()
    s = repr(p)

    # <Person 12345: M(50)>
    assert s.startswith("<Person ")
    assert ":" in s
    assert "(" in s


def test_age_property(session):
    """Test age property."""
    p = session.scalars(sa.select(PersonView).limit(1)).first()
    assert p.age is None or p.age > 0


def test_age_at_expression(session):
    """Test age at expression."""
    q = sa.select(PersonView).where(PersonView.age_at(date(2020, 1, 1)) > 0).limit(5)
    rows = session.scalars(q).all()
    assert len(rows) > 0


def test_observation_period_flags(session):
    """Test observation period flags."""
    p = session.scalars(sa.select(PersonView).limit(1)).first()

    assert isinstance(p.has_observation_period, bool)
    assert isinstance(p.is_deceased, bool)


def test_person_domain_valid(session):
    """Test person domain valid."""
    p = session.scalars(sa.select(PersonView).limit(1)).first()
    assert p.is_domain_valid
    assert p.domain_violations == []


def test_domain_validation_detects_violation(session):
    """Test domain validation detects violation."""
    p = session.scalars(sa.select(PersonView).limit(1)).first()

    # Force a wrong domain (e.g. use a Race concept as gender)
    wrong = session.scalar(sa.select(Concept.concept_id).where(Concept.domain_id == "Race"))

    p.gender_concept_id = wrong

    assert not p.is_domain_valid
    assert any("gender_concept_id" in v for v in p.domain_violations)
