"""DuckDBBackend: the maintenance-ABC methods that are actually implementable
on DuckDB (statistics, index/sequence introspection, anti-join FK violation
counting, in-process backup/restore). Methods with no DuckDB equivalent
(triggers, CLUSTER, tsvector fulltext) are left raising
FeatureNotSupportedError, same as SQLite."""

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


@pytest.mark.parametrize(
    ("sql_text", "expected"),
    [
        ('\'(lower("concept_name"))\'', "lower(concept_name)"),
        ("lower(concept_name)", "lower(concept_name)"),
        ('\'(lower("a"), lower("b"))\'', "lower(a),lower(b)"),
    ],
)
def test_duckdb_backend_normalize_index_expression(sql_text, expected):
    assert DuckDBBackend().normalize_index_expression(sql_text) == expected


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
def test_duckdb_backend_set_sequence_value_roundtrip(duckdb_person_table):
    """set_sequence_value repoints person_id's DEFAULT at a sequence
    starting from the requested value, surviving the swap of the
    underlying sequence object."""
    backend = DuckDBBackend()
    with duckdb_person_table.connect() as conn:
        conn.execute(sa.text("INSERT INTO domain (domain_id, domain_name, domain_concept_id) VALUES ('x', 'x', 0)"))
        conn.execute(
            sa.text("INSERT INTO vocabulary (vocabulary_id, vocabulary_name, vocabulary_concept_id) VALUES ('x', 'x', 0)")
        )
        conn.execute(
            sa.text(
                "INSERT INTO concept_class (concept_class_id, concept_class_name, concept_class_concept_id) "
                "VALUES ('x', 'x', 0)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO concept (concept_id, concept_name, domain_id, vocabulary_id, "
                "concept_class_id, concept_code, valid_start_date, valid_end_date) "
                "VALUES (0, 'test', 'x', 'x', 'x', 'x', '2000-01-01', '2000-01-01')"
            )
        )
        conn.commit()

        sequence_name = backend.find_sequence_name(conn, "person", "person_id")
        assert sequence_name is not None

        backend.set_sequence_value(conn, sequence_name, 1000, table_name="person", column_name="person_id")
        conn.commit()

        conn.execute(
            sa.text(
                "INSERT INTO person (gender_concept_id, year_of_birth, race_concept_id, ethnicity_concept_id) "
                "VALUES (0, 2000, 0, 0)"
            )
        )
        next_id = conn.execute(sa.text("SELECT person_id FROM person")).scalar_one()
        assert next_id == 1000

        # find_sequence_name still resolves the (recreated) sequence under its original name.
        assert backend.find_sequence_name(conn, "person", "person_id") == sequence_name


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_duckdb_backend_count_fk_violations_on_a_deferred_fk(duckdb_person_table):
    """Circular FK are deferred on DuckDB during DDL. count_fk_violations still detects violations 
    through anti-join.
    """
    

    backend = DuckDBBackend()
    with duckdb_person_table.connect() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO domain (domain_id, domain_name, domain_concept_id) "
                "VALUES ('Drug', 'Drug', 999)"
            )
        )
        conn.commit()

        violations = backend.count_fk_violations(
            conn,
            "domain",
            "concept",
            ["domain_concept_id"],
            ["concept_id"],
        )
        assert violations == 1


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_duckdb_backend_truncate_table_batch(duckdb_person_table):
    backend = DuckDBBackend()
    with duckdb_person_table.connect() as conn:
        conn.execute(sa.text("INSERT INTO domain (domain_id, domain_name, domain_concept_id) VALUES ('x', 'x', 0)"))
        conn.execute(
            sa.text("INSERT INTO vocabulary (vocabulary_id, vocabulary_name, vocabulary_concept_id) VALUES ('x', 'x', 0)")
        )
        conn.execute(
            sa.text(
                "INSERT INTO concept_class (concept_class_id, concept_class_name, concept_class_concept_id) "
                "VALUES ('x', 'x', 0)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO concept (concept_id, concept_name, domain_id, vocabulary_id, "
                "concept_class_id, concept_code, valid_start_date, valid_end_date) "
                "VALUES (0, 'test', 'x', 'x', 'x', 'x', '2000-01-01', '2000-01-01')"
            )
        )
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
def test_duckdb_backend_truncate_table_batch_orders_parent_after_child(duckdb_person_table):
    """Truncating [person, condition_occurrence], in that (parent-first)
    order, must still succeed: DuckDB checks FK constraints per statement, 
    so truncating person while condition_occurrence still references it would otherwise fail."""
    backend = DuckDBBackend()
    with duckdb_person_table.connect() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO domain (domain_id, domain_name, domain_concept_id) VALUES ('x', 'x', 0)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO vocabulary (vocabulary_id, vocabulary_name, vocabulary_concept_id) VALUES ('x', 'x', 0)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO concept_class (concept_class_id, concept_class_name, concept_class_concept_id) "
                "VALUES ('x', 'x', 0)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO concept (concept_id, concept_name, domain_id, vocabulary_id, "
                "concept_class_id, concept_code, valid_start_date, valid_end_date) "
                "VALUES (0, 'test', 'x', 'x', 'x', 'x', '2000-01-01', '2000-01-01')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO person (person_id, gender_concept_id, year_of_birth, "
                "race_concept_id, ethnicity_concept_id) VALUES (1, 0, 2000, 0, 0)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO condition_occurrence (condition_occurrence_id, person_id, condition_concept_id, "
                "condition_start_date, condition_type_concept_id) VALUES (1, 1, 0, '2020-01-01', 0)"
            )
        )
        conn.commit()

        # Deliberately parent-first: the fix must reorder this internally.
        backend.truncate_table_batch(
            conn,
            [(Role.PRIMARY.value, "person"), (Role.PRIMARY.value, "condition_occurrence")],
            restart_identities=False,
            cascade=False,
        )
        conn.commit()

        assert conn.execute(sa.text("SELECT COUNT(*) FROM person")).scalar_one() == 0
        assert conn.execute(sa.text("SELECT COUNT(*) FROM condition_occurrence")).scalar_one() == 0


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_duckdb_backend_truncate_table_batch_rejects_cascade(duckdb_person_table):
    backend = DuckDBBackend()
    with duckdb_person_table.connect() as conn, pytest.raises(FeatureNotSupportedError):
        backend.truncate_table_batch(conn, [(Role.PRIMARY.value, "person")], restart_identities=False, cascade=True)


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_duckdb_backend_truncate_table_batch_restarts_identities(duckdb_person_table):
    """restart_identities=True resets person_id's sequence back to its
    original START value, not just whatever MAX(person_id)+1 was before
    truncation."""
    backend = DuckDBBackend()
    with duckdb_person_table.connect() as conn:
        conn.execute(sa.text("INSERT INTO domain (domain_id, domain_name, domain_concept_id) VALUES ('x', 'x', 0)"))
        conn.execute(
            sa.text("INSERT INTO vocabulary (vocabulary_id, vocabulary_name, vocabulary_concept_id) VALUES ('x', 'x', 0)")
        )
        conn.execute(
            sa.text(
                "INSERT INTO concept_class (concept_class_id, concept_class_name, concept_class_concept_id) "
                "VALUES ('x', 'x', 0)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO concept (concept_id, concept_name, domain_id, vocabulary_id, "
                "concept_class_id, concept_code, valid_start_date, valid_end_date) "
                "VALUES (0, 'test', 'x', 'x', 'x', 'x', '2000-01-01', '2000-01-01')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO person (person_id, gender_concept_id, year_of_birth, "
                "race_concept_id, ethnicity_concept_id) VALUES (1, 0, 2000, 0, 0)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO person (person_id, gender_concept_id, year_of_birth, "
                "race_concept_id, ethnicity_concept_id) VALUES (50, 0, 2000, 0, 0)"
            )
        )
        conn.commit()

        backend.truncate_table_batch(conn, [(Role.PRIMARY.value, "person")], restart_identities=True, cascade=False)
        conn.commit()

        conn.execute(
            sa.text(
                "INSERT INTO person (gender_concept_id, year_of_birth, race_concept_id, ethnicity_concept_id) "
                "VALUES (0, 2000, 0, 0)"
            )
        )
        next_id = conn.execute(sa.text("SELECT person_id FROM person")).scalar_one()
        assert next_id == 1
