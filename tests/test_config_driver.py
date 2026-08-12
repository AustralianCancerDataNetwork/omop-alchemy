"""Tests for typed CDM resolution, engine creation, and cache registration."""
import sqlalchemy.orm as so
from oa_configurator import (
    CDMDatabaseConfig,
    ConnectionConfig,
    ResolvedCDMDatabase,
    StackConfig,
)

from omop_alchemy.config import (
    create_cdm_engine,
    get_cdm_context,
    vocabulary_identity,
)
from omop_alchemy.toolkit.core.concepts import clear_vocabulary_identity
from omop_alchemy.toolkit.core.concepts.identity import cache_scope


def _resolved_cdm_database(
    *,
    primary_database: str,
    vocab_database: str | None = None,
    vocab_schema: str = "main",  # default co-located: matches schema_name below
) -> ResolvedCDMDatabase:
    primary = ConnectionConfig(
        dialect="sqlite",
        database_name=primary_database,
    ).resolve("primary")
    vocab = ConnectionConfig(
        dialect="sqlite",
        database_name=vocab_database or primary_database,
    ).resolve("vocab")
    return ResolvedCDMDatabase(
        name="cdm_db",
        connection=primary,
        schema_name="main",
        vocab_connection=vocab,
        vocab_schema=vocab_schema,
        results_schema=None,
    )


def test_create_cdm_engine_supports_sqlite():
    resolved = _resolved_cdm_database(primary_database=":memory:")
    engine = create_cdm_engine(resolved)
    engine.dispose()


def test_get_cdm_context_resolves_the_typed_database_field(monkeypatch) -> None:
    stack = StackConfig.for_session(
        connections={
            "cdm": ConnectionConfig(dialect="sqlite", database_name=":memory:")
        },
        databases={
            "cdm_db": CDMDatabaseConfig(connection="cdm", schema_name="main")
        },
    )
    monkeypatch.setattr("omop_alchemy.config.load_stack_config", lambda: stack)

    package_config, resolved = get_cdm_context()

    assert package_config.cdm_db == "cdm_db"
    assert isinstance(resolved, ResolvedCDMDatabase)
    assert resolved.schema_name == "main"


def test_vocabulary_identity_for_colocated_vocabulary() -> None:
    """The normal case: vocabulary lives with the CDM, so expansions are shareable."""
    resolved = _resolved_cdm_database(primary_database="primary.db")

    assert vocabulary_identity(resolved) == (
        f"{resolved.vocab_connection.safe_url}|main"
    )


def test_vocabulary_identity_is_none_for_a_split_vocab_connection() -> None:
    """A declared vocabulary connection the engine cannot reach is not an identity.

    One engine cannot route tables to a second physical connection, so the
    primary is what actually gets read.
    """
    resolved = _resolved_cdm_database(
        primary_database="primary.db",
        vocab_database="vocab.db",
    )

    assert vocabulary_identity(resolved) is None


def test_vocabulary_identity_is_none_for_a_split_vocab_schema() -> None:
    """Same for a vocabulary schema the models do not use."""
    resolved = _resolved_cdm_database(
        primary_database="primary.db",
        vocab_schema="omop_vocab",
    )

    assert vocabulary_identity(resolved) is None


def test_distinct_primaries_naming_one_vocabulary_do_not_collide() -> None:
    """Two CDM databases citing the same external vocabulary must not share a cache.

    Both would compose the same vocab-role identity, so if the safety condition
    lived only in create_cdm_engine, any other registrar would let one database's
    concept sets be served for the other.
    """
    alpha = _resolved_cdm_database(
        primary_database="cdm_alpha.db", vocab_database="shared_vocab.db"
    )
    beta = _resolved_cdm_database(
        primary_database="cdm_beta.db", vocab_database="shared_vocab.db"
    )

    assert alpha.connection.safe_url != beta.connection.safe_url
    assert vocabulary_identity(alpha) is None
    assert vocabulary_identity(beta) is None


def test_vocabulary_identity_skips_in_memory_sqlite() -> None:
    resolved = _resolved_cdm_database(primary_database=":memory:")

    assert vocabulary_identity(resolved) is None


def test_create_cdm_engine_registers_the_returned_engine(tmp_path) -> None:
    database = str(tmp_path / "cdm.db")
    resolved = _resolved_cdm_database(
        primary_database=database,
        vocab_database=database,
        vocab_schema="main",
    )
    expected_identity = vocabulary_identity(resolved)
    engine = create_cdm_engine(resolved)

    try:
        with so.Session(engine) as session:
            assert cache_scope(session) == expected_identity
    finally:
        clear_vocabulary_identity(engine)
        engine.dispose()


def test_create_cdm_engine_does_not_register_a_split_vocabulary(tmp_path) -> None:
    resolved = _resolved_cdm_database(
        primary_database=str(tmp_path / "cdm.db"),
        vocab_database=str(tmp_path / "vocab.db"),
        vocab_schema="omop_vocab",
    )
    engine = create_cdm_engine(resolved)

    try:
        with so.Session(engine) as session:
            assert cache_scope(session) is engine
    finally:
        engine.dispose()
