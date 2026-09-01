"""
PostgreSQL integration tests for OMOP_Alchemy vocabulary loading.

These tests require a running PostgreSQL container. Start one with:
    docker compose -f tests/docker-compose.yaml up -d

Excluded from the default `pytest` invocation (addopts = "-m 'not
db_dialect'", see oa_configurator.testing). Run explicitly:
    pytest -m postgresql
"""

from pathlib import Path

import pytest
import sqlalchemy as sa

from omop_alchemy.backends.postgres import PostgresBackend
from omop_alchemy.cdm.model.vocabulary import Concept
from omop_alchemy.maintenance.cli_vocab import (
    _load_vocab_model_csv,
    load_vocab_source,
)
from tests.conftest import _ATHENA_FIXTURE_DATA, _write_fixture_csv

pytestmark = [pytest.mark.postgresql, pytest.mark.db_dialect]


def _copy_fixture_source(base_dir: Path) -> Path:
    """Write the shared in-memory Athena fixture set into an isolated per-test source dir."""
    source_path = base_dir / "athena_source"
    source_path.mkdir(parents=True)
    for table_name, data in _ATHENA_FIXTURE_DATA.items():
        _write_fixture_csv(source_path, table_name, data)
    return source_path


def _make_concept_source(
    base_dir: Path,
    *,
    concept_id: int,
    concept_name: str,
) -> Path:
    """
    Build a minimal vocabulary source where CONCEPT.csv contains exactly one
    test concept with a Gender domain reference, and all other required tables
    are written from the shared in-memory fixture.
    """
    source_path = base_dir / "athena_source"
    source_path.mkdir(parents=True)

    for table_name, data in _ATHENA_FIXTURE_DATA.items():
        if table_name != "concept":
            _write_fixture_csv(source_path, table_name, data)

    concept_cols = list(_ATHENA_FIXTURE_DATA["concept"].keys())
    concept_row = [
        concept_id,
        concept_name,
        "Gender",
        "Gender",
        "Gender",
        "S",
        "TEST",
        "19700101",
        "20991231",
        None,
    ]
    _write_fixture_csv(
        source_path,
        "concept",
        {col: (val,) for col, val in zip(concept_cols, concept_row)},
    )
    return source_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_end_to_end_vocab_load_on_postgres(pg_session, pg_engine, tmp_path):
    """load_vocab_source() completes end-to-end on real Postgres via orm-loader>=0.4.0."""
    source_path = _copy_fixture_source(tmp_path)
    report = load_vocab_source(pg_engine, source_path=source_path)

    assert report.merge_strategy == "replace"
    assert all(r.status == "loaded" for r in report.results if r.required)
    assert all(r.status == "skipped" for r in report.results if not r.required)

    count = pg_session.execute(sa.text("SELECT COUNT(*) FROM concept")).scalar()
    assert count == 7


def test_default_quote_mode_preserves_literal_quotes_on_postgres(
    pg_session, pg_engine, tmp_path
):
    """
    The default by_delimiter mode preserves quotes in tab-delimited Athena data.
    """
    source_path = tmp_path / "athena_source"
    source_path.mkdir()

    quoted_name = '"BRCA1 positive"'

    # All tables except concept get the standard fixture data.
    for table_name, data in _ATHENA_FIXTURE_DATA.items():
        if table_name != "concept":
            _write_fixture_csv(source_path, table_name, data)

    concept_cols = list(_ATHENA_FIXTURE_DATA["concept"].keys())
    concept_row = [
        1,
        quoted_name,
        "Gender",
        "Gender",
        "Gender",
        "S",
        "TEST",
        "19700101",
        "20991231",
        None,
    ]
    _write_fixture_csv(
        source_path,
        "concept",
        {col: (val,) for col, val in zip(concept_cols, concept_row)},
    )

    load_vocab_source(pg_engine, source_path=source_path)

    concept_name = pg_session.execute(
        sa.text("SELECT concept_name FROM concept WHERE concept_id = 1")
    ).scalar()
    assert concept_name == quoted_name


def test_explicit_csv_quote_mode_strips_quotes_on_postgres(
    pg_session, pg_engine, tmp_path
):
    """Explicit csv mode keeps support for genuinely RFC-4180-wrapped fields."""
    source_path = tmp_path / "athena_source"
    source_path.mkdir()

    long_name = "A" * 255
    for table_name, data in _ATHENA_FIXTURE_DATA.items():
        if table_name != "concept":
            _write_fixture_csv(source_path, table_name, data)

    concept_cols = list(_ATHENA_FIXTURE_DATA["concept"].keys())
    concept_row = [
        1,
        f'"{long_name}"',
        "Gender",
        "Gender",
        "Gender",
        "S",
        "TEST",
        "19700101",
        "20991231",
        None,
    ]
    _write_fixture_csv(
        source_path,
        "concept",
        {col: (val,) for col, val in zip(concept_cols, concept_row)},
    )

    load_vocab_source(pg_engine, source_path=source_path, quote_mode="csv")

    concept_name = pg_session.execute(
        sa.text("SELECT concept_name FROM concept WHERE concept_id = 1")
    ).scalar()
    assert concept_name == long_name


def test_load_vocab_model_csv_on_postgres(pg_session, tmp_path):
    """
    _load_vocab_model_csv loads data correctly on a real PostgreSQL session.

    orm-loader>=0.4.0 handles staging-table creation internally, so we test
    the end-to-end path: CSV → staging → concept table on real Postgres.
    """
    source_path = _copy_fixture_source(tmp_path)
    csv_path = source_path / "CONCEPT.csv"

    row_count = _load_vocab_model_csv(
        pg_session,
        model=Concept,  # type: ignore[arg-type]
        csv_path=csv_path,
        merge_strategy="replace",
    )
    pg_session.commit()

    assert row_count == 7
    count = pg_session.execute(sa.text("SELECT COUNT(*) FROM concept")).scalar()
    assert count == 7


def test_replace_strategy_overwrites_matching_and_preserves_absent_rows(
    pg_session,
    pg_engine,
    tmp_path,
):
    """replace updates matching PKs without deleting rows absent from the next source."""
    concept_id = 99999
    source_absent_id = 99997
    source_absent = _make_concept_source(
        tmp_path / "absent",
        concept_id=source_absent_id,
        concept_name="preserved",
    )
    source_v1 = _make_concept_source(
        tmp_path / "v1", concept_id=concept_id, concept_name="name_v1"
    )
    source_v2 = _make_concept_source(
        tmp_path / "v2", concept_id=concept_id, concept_name="name_v2"
    )

    load_vocab_source(pg_engine, source_path=source_absent, merge_strategy="replace")
    load_vocab_source(pg_engine, source_path=source_v1, merge_strategy="replace")
    load_vocab_source(pg_engine, source_path=source_v2, merge_strategy="replace")

    names = dict(
        pg_session.execute(
            sa.text(
                "SELECT concept_id, concept_name FROM concept "
                "WHERE concept_id IN (:matching_id, :absent_id)"
            ),
            {"matching_id": concept_id, "absent_id": source_absent_id},
        ).all()
    )
    assert names[concept_id] == "name_v2"
    assert names[source_absent_id] == "preserved"


def test_upsert_strategy_is_non_destructive(pg_session, pg_engine, tmp_path):
    """merge_strategy='upsert' preserves existing rows on second load with same PKs."""
    concept_id = 99998
    source_v1 = _make_concept_source(
        tmp_path / "v1", concept_id=concept_id, concept_name="name_v1"
    )
    source_v2 = _make_concept_source(
        tmp_path / "v2", concept_id=concept_id, concept_name="name_v2"
    )

    load_vocab_source(pg_engine, source_path=source_v1, merge_strategy="upsert")
    load_vocab_source(pg_engine, source_path=source_v2, merge_strategy="upsert")

    name = pg_session.execute(
        sa.text("SELECT concept_name FROM concept WHERE concept_id = :cid"),
        {"cid": concept_id},
    ).scalar()
    assert name == "name_v1", (
        f"Expected 'name_v1' after upsert (existing row preserved), got {name!r}"
    )


def test_db_schema_search_path_on_postgres(pg_engine, tmp_path):
    """
    load_vocab_source with db_schema creates vocabulary tables in the requested
    PostgreSQL schema and loads data into them correctly.

    schema_translate_map, not db_schema alone, is what actually routes
    ORM-managed table creation: a real deployment sets it once, at engine
    construction (ResolvedCDMDatabase.create_engine()), not per call. This
    scopes it here the same way, matching orm-loader's own
    test_schema_translate_map.py regression test.
    """
    schema = "VocabTest"
    source_path = _copy_fixture_source(tmp_path)
    quoted_schema = '"' + schema.replace('"', '""') + '"'

    with pg_engine.connect() as conn:
        conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE"))
        conn.execute(sa.text(f"CREATE SCHEMA {quoted_schema}"))
        conn.commit()

    # A single-schema deployment: vocab/results fall back to the same
    # schema as everything else, matching ResolvedCDMDatabase's own default
    # fallback behaviour when vocab_schema/results_schema aren't configured.
    scoped_engine = pg_engine.execution_options(
        schema_translate_map={None: schema, "vocab": schema, "results": schema}
    )

    try:
        report = load_vocab_source(
            scoped_engine,
            source_path=source_path,
            db_schema=schema,
        )

        assert any(r.status == "loaded" for r in report.results if r.required)

        inspector = sa.inspect(pg_engine)
        assert inspector.has_table("concept", schema=schema), (
            f"Expected concept table in schema '{schema}'"
        )

        with pg_engine.connect() as conn:
            count = conn.execute(
                sa.text(f"SELECT COUNT(*) FROM {quoted_schema}.concept")
            ).scalar()
        assert count == 7
    finally:
        with pg_engine.connect() as conn:
            conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE"))
            conn.commit()


def test_postgres_catalog_queries_accept_explicit_schema(pg_engine):
    """Schema-qualified catalog checks must bind cleanly with psycopg/PostgreSQL."""
    backend = PostgresBackend()

    with pg_engine.connect() as connection:
        disabled, enabled = backend.get_fk_trigger_counts(
            connection,
            "concept",
        )
        clustered_index = backend.get_clustered_index_name(
            connection,
            "concept",
        )

    assert disabled >= 0
    assert enabled >= 0
    assert clustered_index is None or isinstance(clustered_index, str)
