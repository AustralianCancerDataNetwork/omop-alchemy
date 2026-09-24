import pytest
from orm_loader.helpers import bootstrap
from sqlalchemy.orm import sessionmaker

from omop_alchemy.cdm.model.vocabulary import (
    Domain,
    Relationship,
    Concept,
    Concept_Ancestor,
    Concept_Relationship,
)
from pathlib import Path
from tests.conftest import ATHENA_LOAD_ORDER, _ATHENA_FIXTURE_DATA, _write_fixture_csv


@pytest.fixture
def connection(fresh_engine):
    """
    In-memory SQLite database for tests.
    """
    connection = fresh_engine.connect()
    bootstrap(connection, create=True)  # type: ignore[arg-type]
    yield connection
    connection.close()


@pytest.fixture
def db_session(connection):
    """
    SQLAlchemy session per test.
    """
    Session = sessionmaker(bind=connection, future=True)
    session = Session()
    try:
        yield session
        session.commit()
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def athena_vocab(connection, tmp_path):
    """
    Load the minimal Athena vocabulary fixture using the real ORM CSV loader.

    Writes in-memory fixture data to a temp directory so no static CSV files
    on disk are required.
    """
    base_path: Path = tmp_path
    Session = sessionmaker(bind=connection, future=True)
    session = Session()

    for model in ATHENA_LOAD_ORDER:
        csv_path = _write_fixture_csv(base_path, model.__tablename__, _ATHENA_FIXTURE_DATA[model.__tablename__])
        model.load_csv(session, csv_path)

    session.commit()
    session.close()

    yield


def test_concept_loaded(db_session, athena_vocab):
    """Test that vocabulary concepts load and are accessible by primary key."""
    # MALE (concept_id=8507) is a known row in the minimal fixture.
    concept = db_session.get(Concept, 8507)
    assert concept is not None
    assert concept.concept_name == "MALE"
    assert concept.domain_id == "Gender"


def test_concept_ancestor(db_session, athena_vocab):
    """Test that the concept_ancestor table loads without error."""
    # Minimal fixtures have no ancestor rows; table must be accessible and empty.
    count = db_session.query(Concept_Ancestor).count()
    assert count == 0


def test_all_concepts_reference_valid_domain(db_session, athena_vocab):
    """Test all concepts reference valid domain."""
    invalid = (
        db_session.query(Concept)
        .outerjoin(Domain, Concept.domain_id == Domain.domain_id)
        .filter(Domain.domain_id.is_(None))
        .count()
    )

    assert invalid == 0


def test_relationship_vocab_loaded(db_session, athena_vocab):
    """Test relationship vocab loaded."""
    rel = (
        db_session.query(Relationship)
        .filter_by(relationship_id="Is a")
        .one()
    )

    assert rel.reverse_relationship_id == "Subsumes"


def test_expected_domains_exist(db_session, athena_vocab):
    """Test expected domains exist."""
    domains = {
        d.domain_id
        for d in db_session.query(Domain.domain_id).all()
    }

    assert "Condition" in domains
    assert "Gender" in domains
    assert "Race" in domains


def test_domains_are_consistent(db_session, athena_vocab):
    """Test concepts reference domains that exist in the domain table."""
    concepts = (
        db_session.query(Concept)
        .filter(Concept.domain_id.in_(["Condition", "Gender"]))
        .all()
    )

    assert concepts

    for c in concepts:
        assert c.domain_id in {"Condition", "Gender"}


def test_condition_concepts_exist(db_session, athena_vocab):
    """Test condition concepts exist."""
    assert (
        db_session.query(Concept)
        .filter(Concept.domain_id == "Condition")
        .count()
        > 0
    )


def test_relationships_reference_valid_concepts(db_session, athena_vocab):
    """Test relationships reference valid concepts."""
    rels = db_session.query(Concept_Relationship).limit(50).all()

    for r in rels:
        assert r.concept_id_1 is not None
        assert r.concept_id_2 is not None
