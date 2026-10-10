"""Tests for typed CDM resolution, engine creation, and the concept cache scope."""
from oa_configurator import (
    CDMDatabaseConfig,
    ConnectionConfig,
    ResolvedCDMDatabase,
    StackConfig,
)

from omop_alchemy.config import (
    create_cdm_engines,
    get_cdm_context,
)
from omop_alchemy.cross_database import cdm_sessionmaker
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
    vocab = (
        primary
        if vocab_database is None
        else ConnectionConfig(
            dialect="sqlite",
            database_name=vocab_database,
        ).resolve("vocab")
    )
    return ResolvedCDMDatabase(
        name="cdm_db",
        connection=primary,
        schema_name="main",
        vocab_connection=vocab,
        vocab_schema=vocab_schema,
        results_schema=None,
    )


def test_create_cdm_engines_supports_sqlite():
    resolved = _resolved_cdm_database(primary_database=":memory:")
    primary, vocab = create_cdm_engines(resolved)
    assert vocab is primary
    primary.dispose()


def test_create_cdm_engines_keeps_distinct_in_memory_connections_separate():
    resolved = _resolved_cdm_database(
        primary_database=":memory:", vocab_database=":memory:"
    )
    primary, vocab = create_cdm_engines(resolved)
    try:
        assert primary is not vocab
    finally:
        primary.dispose()
        vocab.dispose()


def test_get_cdm_context_resolves_the_typed_database_field(monkeypatch) -> None:
    stack = StackConfig.for_session(
        connections={
            "cdm": ConnectionConfig(dialect="sqlite", database_name=":memory:")
        },
        databases={"cdm_db": CDMDatabaseConfig(connection="cdm")},
    )
    monkeypatch.setattr("omop_alchemy.config.load_stack_config", lambda: stack)

    package_config, resolved = get_cdm_context()

    assert package_config.cdm_db == "cdm_db"
    assert isinstance(resolved, ResolvedCDMDatabase)
    assert resolved.schema_name is None


def _scope(resolved: ResolvedCDMDatabase) -> object:
    """cache_scope of a routed session on *resolved*'s engines."""
    primary, vocab = create_cdm_engines(resolved)
    try:
        with cdm_sessionmaker(resolved, primary=primary, vocab=vocab)() as session:
            scope = cache_scope(session)
            return "engine" if scope is vocab else scope
    finally:
        primary.dispose()
        vocab.dispose()


def test_cache_scope_for_a_colocated_vocabulary(tmp_path) -> None:
    database = str(tmp_path / "cdm.db")
    resolved = _resolved_cdm_database(primary_database=database)

    assert _scope(resolved) == f":/{database}|"


def test_cache_scope_for_a_split_vocabulary_is_the_vocabulary_database(tmp_path) -> None:
    vocab_database = str(tmp_path / "vocab.db")
    resolved = _resolved_cdm_database(
        primary_database=str(tmp_path / "cdm.db"), vocab_database=vocab_database
    )

    assert _scope(resolved) == f":/{vocab_database}|"


def test_cache_scope_resolves_relative_sqlite_paths(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    first = _resolved_cdm_database(primary_database="./vocab.db")
    second = _resolved_cdm_database(primary_database="vocab.db")

    assert _scope(first) == _scope(second)


def test_primaries_sharing_one_vocabulary_share_a_scope(tmp_path) -> None:
    """Expansions depend only on the vocabulary, so both CDMs reuse them."""
    shared = str(tmp_path / "shared_vocab.db")
    alpha = _resolved_cdm_database(primary_database=str(tmp_path / "alpha.db"), vocab_database=shared)
    beta = _resolved_cdm_database(primary_database=str(tmp_path / "beta.db"), vocab_database=shared)

    assert _scope(alpha) == _scope(beta)


def test_in_memory_sqlite_is_scoped_to_its_engine() -> None:
    resolved = _resolved_cdm_database(primary_database=":memory:")

    assert _scope(resolved) == "engine"
