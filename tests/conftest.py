import copy
from datetime import date
from pathlib import Path
import pytest
import sqlalchemy as sa
from orm_loader.helpers import bootstrap
import sqlalchemy.orm as so
from sqlalchemy.orm import Session, sessionmaker

from typing import Any, Dict, Tuple

from omop_alchemy.maintenance.cli_vocab import _load_vocab_model_csv
from omop_alchemy.cdm.model.clinical import Condition_Occurrence, Person
from omop_alchemy.cdm.model.derived import Observation_Period
from omop_alchemy.cdm.model.structural import Episode, Episode_Event
from omop_alchemy.cdm.model.vocabulary import (
    Concept,
    Concept_Ancestor,
    Concept_Class,
    Concept_Relationship,
    Domain,
    Relationship,
    Vocabulary,
)


ATHENA_LOAD_ORDER = [
    Domain,
    Vocabulary,
    Concept_Class,
    Relationship,
    Concept,
    Concept_Ancestor,
    Concept_Relationship,
]

# ---------------------------------------------------------------------------
# In-memory Athena fixture data
# ---------------------------------------------------------------------------
# Keyed by ORM __tablename__. Each value is a dict mapping column name →
# tuple of row values (one entry per row, in the same order).
# Empty tuples = table has no rows (header-only CSV).

_ATHENA_FIXTURE_DATA: Dict[str, Dict[str, Tuple[Any, ...]]] = {
    "concept_ancestor": {
        "ancestor_concept_id": (),
        "descendant_concept_id": (),
        "min_levels_of_separation": (),
        "max_levels_of_separation": (),
    },
    "concept_class": {
        "concept_class_id": ("Clinical Finding", "Episode", "Ethnicity", "Field", "Gender", "Race", "Type Concept"),
        "concept_class_name": ("Clinical Finding", "Episode", "Ethnicity", "Field", "Gender", "Race", "Type Concept"),
        "concept_class_concept_id": (0, 0, 0, 0, 0, 0, 0),
    },
    "concept_relationship": {
        "concept_id_1": (),
        "concept_id_2": (),
        "relationship_id": (),
        "valid_start_date": (),
        "valid_end_date": (),
        "invalid_reason": (),
    },
    "concept_synonym": {
        "concept_id": (),
        "concept_synonym_name": (),
        "language_concept_id": (),
    },
    "concept": {
        "concept_id": (8507, 8527, 38003564, 32817, 201826, 32546, 1147127),
        "concept_name": (
            "MALE",
            "White",
            "Not Hispanic or Latino",
            "EHR",
            "Type 2 diabetes mellitus",
            "Disease Episode",
            "condition_occurrence.condition_occurrence_id",
        ),
        "domain_id": ("Gender", "Race", "Ethnicity", "Type Concept", "Condition", "Episode", "Metadata"),
        "vocabulary_id": ("Gender", "Race", "Ethnicity", "Type Concept", "SNOMED", "Episode", "CDM"),
        "concept_class_id": (
            "Gender",
            "Race",
            "Ethnicity",
            "Type Concept",
            "Clinical Finding",
            "Episode",
            "Field",
        ),
        "standard_concept": ("S", "S", "S", "S", "S", "S", "S"),
        "concept_code": (
            "M",
            "White",
            "Not Hispanic or Latino",
            "EHR",
            "44054006",
            "Disease Episode",
            "condition_occurrence.condition_occurrence_id",
        ),
        "valid_start_date": (
            "19700101",
            "19700101",
            "19700101",
            "19700101",
            "19700101",
            "19700101",
            "19700101",
        ),
        "valid_end_date": (
            "20991231",
            "20991231",
            "20991231",
            "20991231",
            "20991231",
            "20991231",
            "20991231",
        ),
        "invalid_reason": (None, None, None, None, None, None, None),
    },
    "domain": {
        "domain_id": ("Condition", "Episode", "Ethnicity", "Gender", "Metadata", "Race", "Type Concept"),
        "domain_name": ("Condition", "Episode", "Ethnicity", "Gender", "Metadata", "Race", "Type Concept"),
        "domain_concept_id": (0, 0, 0, 0, 0, 0, 0),
    },
    "relationship": {
        "relationship_id": ("Is a", "Subsumes"),
        "relationship_name": ("Is a", "Subsumes"),
        "is_hierarchical": (1, 1),
        "defines_ancestry": (1, 0),
        "reverse_relationship_id": ("Subsumes", "Is a"),
        "relationship_concept_id": (0, 0),
    },
    "vocabulary": {
        "vocabulary_id": ("CDM", "Episode", "Ethnicity", "Gender", "Race", "SNOMED", "Type Concept"),
        "vocabulary_name": (
            "Common Data Model",
            "OMOP Episode",
            "OMOP Ethnicity",
            "OMOP Gender",
            "OMOP Race",
            "SNOMED-CT",
            "OMOP Type Concept",
        ),
        "vocabulary_reference": ("OHDSI", "OHDSI", "OHDSI", "OHDSI", "OHDSI", "IHTSDO", "OHDSI"),
        "vocabulary_version": ("v5.4", "v1.0", "v1.0", "v1.0", "v1.0", "SNOMED CT 2023", "v1.0"),
        "vocabulary_concept_id": (0, 0, 0, 0, 0, 0, 0),
    },
}


def _write_fixture_csv(directory: Path, table_name: str, data: Dict[str, Tuple[Any, ...]]) -> Path:
    """Write an in-memory fixture dict to a tab-separated CSV file."""
    path = directory / f"{table_name.upper()}.csv"
    cols = list(data.keys())
    rows = list(zip(*data.values())) if cols and any(data.values()) else []
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write("\t".join(cols) + "\n")
        for row in rows:
            f.write("\t".join("" if v is None else str(v) for v in row) + "\n")
    return path


def _load_fixture_vocabulary(engine: sa.Engine, tmp_dir: Path) -> None:
    """Write in-memory Athena fixtures to tmp_dir and load them into the test database."""
    with engine.connect() as connection:
        SessionLocal = sessionmaker(bind=connection)
        session = SessionLocal()
        try:
            for model in ATHENA_LOAD_ORDER:
                csv_path = _write_fixture_csv(
                    tmp_dir, model.__tablename__, _ATHENA_FIXTURE_DATA[model.__tablename__]
                )
                _load_vocab_model_csv(
                    session,
                    model=model,
                    csv_path=csv_path,
                    merge_strategy="upsert",
                    quote_mode="auto",
                )
                session.commit()
            connection.commit()
        finally:
            session.close()


def _concept_id(
    session: Session,
    *,
    concept_name: str | None = None,
    domain_id: str | None = None,
) -> int:
    """Resolve a concept_id from seeded fixtures, raising if not found."""
    query = sa.select(Concept.concept_id)

    if concept_name is not None:
        query = query.where(Concept.concept_name == concept_name)
    if domain_id is not None:
        query = query.where(Concept.domain_id == domain_id)

    concept_id = session.scalar(query.order_by(Concept.concept_id).limit(1))
    if concept_id is None:
        raise RuntimeError(
            f"Missing concept fixture for concept_name={concept_name!r}, "
            f"domain_id={domain_id!r}"
        )
    return int(concept_id)


def _seed_basic_clinical_data(session: Session) -> None:
    """Insert a compact clinical graph used by core model relationship tests."""
    male_concept_id = _concept_id(session, concept_name="MALE", domain_id="Gender")
    race_concept_id = _concept_id(session, concept_name="White", domain_id="Race")
    ethnicity_concept_id = _concept_id(
        session,
        concept_name="Not Hispanic or Latino",
        domain_id="Ethnicity",
    )
    type_concept_id = _concept_id(session, domain_id="Type Concept")
    condition_concept_id = _concept_id(session, domain_id="Condition")
    episode_concept_id = _concept_id(session, domain_id="Episode")
    event_field_concept_id = _concept_id(
        session,
        concept_name="condition_occurrence.condition_occurrence_id",
        domain_id="Metadata",
    )

    session.add(
        Person(
            person_id=1,
            year_of_birth=1980,
            month_of_birth=1,
            day_of_birth=1,
            gender_concept_id=male_concept_id,
            race_concept_id=race_concept_id,
            ethnicity_concept_id=ethnicity_concept_id,
            person_source_value="fixture-person-1",
        )
    )
    session.add(
        Observation_Period(
            observation_period_id=1,
            person_id=1,
            observation_period_start_date=date(2019, 1, 1),
            observation_period_end_date=date(2021, 12, 31),
            period_type_concept_id=type_concept_id,
        )
    )
    session.add(
        Condition_Occurrence(
            condition_occurrence_id=1,
            person_id=1,
            condition_concept_id=condition_concept_id,
            condition_start_date=date(2020, 1, 1),
            condition_end_date=date(2020, 1, 15),
            condition_type_concept_id=type_concept_id,
            condition_source_value="fixture-condition-1",
        )
    )
    session.add(
        Episode(
            episode_id=100,
            person_id=1,
            episode_start_date=date(2020, 1, 1),
            episode_end_date=date(2020, 1, 31),
            episode_concept_id=episode_concept_id,
            episode_object_concept_id=condition_concept_id,
            episode_type_concept_id=type_concept_id,
            episode_source_value="fixture-parent-episode",
        )
    )
    session.add(
        Episode(
            episode_id=101,
            episode_parent_id=100,
            person_id=1,
            episode_start_date=date(2020, 1, 2),
            episode_end_date=date(2020, 1, 20),
            episode_concept_id=episode_concept_id,
            episode_object_concept_id=condition_concept_id,
            episode_type_concept_id=type_concept_id,
            episode_source_value="fixture-child-episode",
        )
    )
    session.add(
        Episode_Event(
            episode_id=101,
            event_id=1,
            episode_event_field_concept_id=event_field_concept_id,
        )
    )

    session.commit()


@pytest.fixture(scope="session")
def engine(tmp_path_factory: pytest.TempPathFactory):
    """
    Session-scoped SQLite engine built from repo fixtures.

    The database is created fresh for each test session, so it behaves
    the same on the host and inside containers.
    """
    db_dir = tmp_path_factory.mktemp("omop-alchemy")
    db_path = db_dir / "test.db"
    engine = sa.create_engine(
        f"sqlite:///{db_path}",
        future=True,
        echo=False,
        poolclass=sa.pool.StaticPool,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    bootstrap(engine, create=True)
    _load_fixture_vocabulary(engine, db_dir)

    with so.Session(engine, expire_on_commit=False) as seed_session:
        _seed_basic_clinical_data(seed_session)

    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def pg_engine():
    """Session-scoped PostgreSQL engine for integration tests.

    Resolves via OA_Configurator resource 'test_cdm_db' in ~/.config/omop/config.toml.
    Run: omop-config configure omop_alchemy (answer Y when asked to configure test database).
    """
    from oa_configurator.pytest_plugin import (
        ensure_test_db_exists,
        ensure_test_user_exists,
        resolve_test_database,
    )
    from omop_alchemy.config import OmopAlchemyConfig
    url = resolve_test_database(OmopAlchemyConfig, "test_cdm_db")

    ensure_test_user_exists(url)
    ensure_test_db_exists(url)
    engine = sa.create_engine(url, future=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def pg_session(pg_engine):
    """
    Function-scoped PostgreSQL session with a clean schema for each test.

    Drops and recreates the public schema before each test to ensure full isolation.
    """
    with pg_engine.connect() as conn:
        conn.execute(sa.text("DROP SCHEMA public CASCADE"))
        conn.execute(sa.text("CREATE SCHEMA public"))
        conn.commit()

    bootstrap(pg_engine, create=True)

    session = so.Session(pg_engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(scope="function")
def session(engine) -> Session:  # type: ignore
    """
    Function-scoped SQLAlchemy session.

    Each test gets a clean transactional boundary.
    """
    session = so.Session(engine, expire_on_commit=False)

    try:
        yield session  # type: ignore
    finally:
        session.rollback()
        session.close()


# ---------------------------------------------------------------------------
# In-memory Athena vocabulary fixtures
# ---------------------------------------------------------------------------
# Each fixture returns a mutable copy of the module-level constant so tests
# can append rows without cross-contaminating other tests.


@pytest.fixture(scope="function")
def athena_fixtures() -> Dict[str, Dict[str, Tuple[Any, ...]]]:
    """All Athena vocabulary tables as a single dict keyed by ORM table name."""
    return {k: dict(v) for k, v in _ATHENA_FIXTURE_DATA.items()}


@pytest.fixture(scope="function")
def athena_source_dir(tmp_path: Path) -> Path:
    """Write in-memory Athena fixtures to a temp directory and return the path."""
    source = tmp_path / "athena_source"
    source.mkdir()
    for table_name, data in _ATHENA_FIXTURE_DATA.items():
        _write_fixture_csv(source, table_name, data)
    return source


@pytest.fixture(scope="function")
def concept_ancestor() -> Dict[str, Tuple[Any, ...]]:
    """Mutable copy of the concept_ancestor fixture data."""
    return copy.deepcopy(_ATHENA_FIXTURE_DATA["concept_ancestor"])


@pytest.fixture(scope="function")
def concept_class() -> Dict[str, Tuple[Any, ...]]:
    """Mutable copy of the concept_class fixture data."""
    return copy.deepcopy(_ATHENA_FIXTURE_DATA["concept_class"])


@pytest.fixture(scope="function")
def concept_relationship() -> Dict[str, Tuple[Any, ...]]:
    """Mutable copy of the concept_relationship fixture data."""
    return copy.deepcopy(_ATHENA_FIXTURE_DATA["concept_relationship"])


@pytest.fixture(scope="function")
def concept_synonym() -> Dict[str, Tuple[Any, ...]]:
    """Mutable copy of the concept_synonym fixture data."""
    return copy.deepcopy(_ATHENA_FIXTURE_DATA["concept_synonym"])


@pytest.fixture(scope="function")
def concept() -> Dict[str, Tuple[Any, ...]]:
    """Mutable copy of the concept fixture data."""
    return copy.deepcopy(_ATHENA_FIXTURE_DATA["concept"])


@pytest.fixture(scope="function")
def domain() -> Dict[str, Tuple[Any, ...]]:
    """Mutable copy of the domain fixture data."""
    return copy.deepcopy(_ATHENA_FIXTURE_DATA["domain"])


@pytest.fixture(scope="function")
def relationship() -> Dict[str, Tuple[Any, ...]]:
    """Mutable copy of the relationship fixture data."""
    return copy.deepcopy(_ATHENA_FIXTURE_DATA["relationship"])


@pytest.fixture(scope="function")
def vocabulary() -> Dict[str, Tuple[Any, ...]]:
    """Mutable copy of the vocabulary fixture data."""
    return copy.deepcopy(_ATHENA_FIXTURE_DATA["vocabulary"])
