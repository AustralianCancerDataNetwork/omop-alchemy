"""DuckDBBackend: the maintenance-ABC methods that are actually implementable
on DuckDB (statistics, index/sequence introspection, anti-join FK violation
counting). Methods with no DuckDB equivalent (triggers, CLUSTER, tsvector
fulltext, pg_dump/restore) are left raising FeatureNotSupportedError, same
as SQLite."""

import pytest
import sqlalchemy as sa
from oa_configurator import Role

from omop_alchemy.backends.base import FeatureNotSupportedError
from omop_alchemy.backends.duckdb import DuckDBBackend
from omop_alchemy.backends.resolve import resolve_backend
from omop_alchemy.maintenance.cli_schema import _create_missing_tables
from omop_alchemy.maintenance.context import MaintenanceContext


def test_resolve_backend_returns_duckdb_backend():
    engine = sa.create_engine("duckdb:///:memory:")
    backend = resolve_backend(engine)
    assert isinstance(backend, DuckDBBackend)
    assert backend.name == "DuckDB"


@pytest.fixture
def duckdb_person_table(fresh_duckdb_engine, fresh_duckdb_resolved):
    _create_missing_tables(
        MaintenanceContext(
            resolved=fresh_duckdb_resolved, engine=fresh_duckdb_engine, vocab_engine=fresh_duckdb_engine
        ),
        vocabulary_included=True,
    )
    return fresh_duckdb_engine


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_duckdb_backend_analyze_table(duckdb_person_table):
    backend = DuckDBBackend()
    with duckdb_person_table.connect() as conn:
        backend.analyze_table(conn, "person")
        backend.analyze_table(conn, "person", vacuum=True)


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_duckdb_backend_index_exists_roundtrip(duckdb_person_table):
    backend = DuckDBBackend()
    with duckdb_person_table.connect() as conn:
        assert backend.index_exists(conn, "idx_test_person_gender") is False

        conn.execute(sa.text("CREATE INDEX idx_test_person_gender ON person (gender_concept_id)"))
        assert backend.index_exists(conn, "idx_test_person_gender") is True

        backend.drop_index_if_exists(conn, "idx_test_person_gender")
        assert backend.index_exists(conn, "idx_test_person_gender") is False

        # Dropping a nonexistent index is a no-op, not an error.
        backend.drop_index_if_exists(conn, "idx_test_person_gender")


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_duckdb_backend_find_sequence_name(duckdb_person_table):
    backend = DuckDBBackend()
    with duckdb_person_table.connect() as conn:
        sequence_name = backend.find_sequence_name(conn, "person", "person_id")
        assert sequence_name is not None
        assert "person_id_seq" in sequence_name

        assert backend.find_sequence_name(conn, "person", "gender_concept_id") is None


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_duckdb_backend_count_fk_violations_on_a_deferred_fk(duckdb_person_table):
    """person.visit_occurrence_id -> visit_occurrence is part of the genuine
    FK cycle oa-configurator's shim no-ops on DuckDB (see
    test_duckdb_schema_creation.py), so the database itself never rejects an
    orphaned reference. count_fk_violations recovers that check via an
    anti-join instead of reading trigger state, since DuckDB has none."""
    backend = DuckDBBackend()
    with duckdb_person_table.connect() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO person (person_id, gender_concept_id, year_of_birth, "
                "race_concept_id, ethnicity_concept_id, visit_occurrence_id) "
                "VALUES (1, 0, 2000, 0, 0, 999)"
            )
        )
        conn.commit()

        violations = backend.count_fk_violations(
            conn,
            "person",
            "visit_occurrence",
            ["visit_occurrence_id"],
            ["visit_occurrence_id"],
        )
        assert violations == 1


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_duckdb_backend_truncate_table_batch(duckdb_person_table):
    backend = DuckDBBackend()
    with duckdb_person_table.connect() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO person (person_id, gender_concept_id, year_of_birth, "
                "race_concept_id, ethnicity_concept_id) VALUES (1, 0, 2000, 0, 0)"
            )
        )
        conn.commit()

        backend.truncate_table_batch(conn, [(Role.PRIMARY.value, "person")], restart_identities=False, cascade=False)
        conn.commit()

        count = conn.execute(sa.text("SELECT COUNT(*) FROM person")).scalar_one()
        assert count == 0


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_duckdb_backend_truncate_table_batch_rejects_restart_identities(duckdb_person_table):
    """ALTER SEQUENCE ... RESTART WITH is unimplemented on DuckDB, and
    CREATE OR REPLACE SEQUENCE is rejected while a column default still
    depends on it, so there's no way to honor restart_identities=True."""
    backend = DuckDBBackend()
    with duckdb_person_table.connect() as conn, pytest.raises(FeatureNotSupportedError):
        backend.truncate_table_batch(conn, [(Role.PRIMARY.value, "person")], restart_identities=True, cascade=False)
