import pytest

from oa_configurator.testing import DIALECT_PARAMS, isolated_test_schema
from omop_alchemy.backends.sqlite import SQLiteBackend
from omop_alchemy.cdm.base.indexing import omop_index_name
from omop_alchemy.maintenance.cli_indexes import manage_indexes
from omop_alchemy.maintenance.cli_schema import create_missing_tables
from omop_alchemy.maintenance.cli_schema_reconcile import is_blocking_issue, reconcile_schema

PERSON_GENDER_INDEX = omop_index_name("person", "gender_concept_id")
EPISODE_PERSON_INDEX = omop_index_name("episode", "person_id")


@pytest.fixture(params=DIALECT_PARAMS)
def reconcile_engine(request):
    """Every OMOP table created, indexed, and clustered, on both Postgres and SQLite.

    manage_indexes(enable=True) is required on Postgres: create_missing_tables()
    alone creates indexes but never physically CLUSTERs them, so a fresh
    database would otherwise report false cluster drift. It's a harmless
    no-op for clustering on SQLite.

    Notes
    -----
    DIALECT_PARAMS marks each param directly, since the postgresql param's
    dynamic request.getfixturevalue("pg_session") call is invisible to
    pytest's usual fixturenames-based auto-detection.
    """
    if request.param == "postgresql":
        engine = request.getfixturevalue("pg_session").get_bind()
    else:
        engine = request.getfixturevalue("fresh_engine")
    create_missing_tables(engine)
    manage_indexes(engine, enable=True)
    return engine


@pytest.fixture
def fresh_reconcile_engine(fresh_engine):
    """fresh_engine with every OMOP table already created.

    SQLite-only, unlike reconcile_engine above: the tests using this
    fixture monkeypatch SQLiteBackend.get_clustered_index_name directly to
    simulate a physical CLUSTER state, since SQLite has no real clustering
    to test against at all (CLUSTER is a genuine Postgres-only physical
    operation). Parametrizing these onto Postgres would need a real CLUSTER
    call, not a mock swap, so they stay a separate, SQLite-specific fixture.
    """
    create_missing_tables(fresh_engine)
    return fresh_engine


def _person_gender_issues(report):
    return [
        issue
        for issue in report.issues
        if issue.table_name == "person"
        and issue.component == "index"
        and (issue.object_name == PERSON_GENDER_INDEX or issue.actual == "idx_gender")
    ]


def test_reconcile_schema_reports_no_drift_on_fresh_database(reconcile_engine):
    engine = reconcile_engine
    report = reconcile_schema(engine)

    person_result = next(r for r in report.table_results if r.table_name == "person")
    assert person_result.status == "matched"
    assert person_result.issue_count == 0


def test_reconcile_schema_reports_renamed_for_foreign_named_equivalent_index(reconcile_engine):
    engine = reconcile_engine
    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP INDEX {PERSON_GENDER_INDEX}")
        connection.exec_driver_sql("CREATE INDEX idx_gender ON person (gender_concept_id)")

    report = reconcile_schema(engine)
    issues = _person_gender_issues(report)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.status == "renamed"
    assert issue.expected == PERSON_GENDER_INDEX
    assert issue.actual == "idx_gender"


def test_reconcile_schema_renamed_index_does_not_flip_table_to_drifted(reconcile_engine):
    engine = reconcile_engine
    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP INDEX {PERSON_GENDER_INDEX}")
        connection.exec_driver_sql("CREATE INDEX idx_gender ON person (gender_concept_id)")

    report = reconcile_schema(engine)
    person_result = next(r for r in report.table_results if r.table_name == "person")

    assert person_result.status == "matched"
    assert person_result.issue_count == 1


@pytest.mark.postgresql
@pytest.mark.db_dialect
def test_reconcile_schema_reports_relocated_when_table_found_in_another_schema(pg_engine):
    """A table missing from its expected schema but physically present under
    a different one reports RELOCATED, not a plain MISSING.
    """
    with (
        isolated_test_schema(pg_engine, prefix="reconcile_relocated_a") as schema_a,
        isolated_test_schema(pg_engine, prefix="reconcile_relocated_b") as schema_b,
    ):
        engine = pg_engine.execution_options(
            schema_translate_map={None: schema_a, "vocab": schema_a, "results": schema_a}
        )
        # vocabulary_included defaults to True: person's gender_concept_id FK
        # targets a vocab table, so excluding vocab here would leave that FK
        # unresolved and person itself blocked from creation.
        create_missing_tables(engine, db_schema=schema_a)
        with engine.begin() as connection:
            connection.exec_driver_sql(f'ALTER TABLE "{schema_a}".person SET SCHEMA "{schema_b}"')

        report = reconcile_schema(engine, db_schema=schema_a)

        person_result = next(r for r in report.table_results if r.table_name == "person")
        assert person_result.status == "relocated"
        person_issue = next(
            i for i in report.issues if i.table_name == "person" and i.component == "table"
        )
        assert person_issue.status == "relocated"
        # public may also legitimately carry a person table from an
        # unrelated database/test, so assert schema_b is among the
        # relocated schemas rather than the only one reported.
        assert schema_b in person_issue.actual.split(", ")
        assert is_blocking_issue(person_issue)


@pytest.mark.postgresql
@pytest.mark.db_dialect
def test_reconcile_schema_with_resolved_qualifies_each_table_to_its_own_role_schema(pg_engine):
    """A clinical table, a vocab table, and a results table each live in a
    genuinely different physical schema. Passing resolved must compare each
    against its own schema, not one blanket db_schema value, or the vocab
    and results tables report false drift here.
    """
    from oa_configurator import ResolvedCDMDatabase, ResolvedConnection

    with (
        isolated_test_schema(pg_engine, prefix="reconcile_three_primary") as primary_schema,
        isolated_test_schema(pg_engine, prefix="reconcile_three_vocab") as vocab_schema,
        isolated_test_schema(pg_engine, prefix="reconcile_three_results") as results_schema,
    ):
        url = pg_engine.url
        connection = ResolvedConnection(
            name="reconcile_three_conn",
            url=url.render_as_string(hide_password=False),
            safe_url=url.render_as_string(hide_password=True),
            _engine_url=url,
        )
        resolved = ResolvedCDMDatabase(
            name="reconcile_three_db",
            connection=connection,
            schema_name=primary_schema,
            vocab_connection=connection,
            vocab_schema=vocab_schema,
            results_schema=results_schema,
        )
        engine = pg_engine.execution_options(
            schema_translate_map={
                None: primary_schema, "vocab": vocab_schema, "results": results_schema
            }
        )
        create_missing_tables(
            engine, db_schema=primary_schema, resolved=resolved, test_only=True
        )

        report = reconcile_schema(engine, resolved=resolved, vocabulary_included=True)

        # cluster/index issues are excluded here. manage_indexes()/
        # cli_indexes.py's cluster commands have their own, separate
        # cross-schema bug (found while writing this test, not yet
        # investigated). This test only verifies that reconcile_schema
        # resolves each table's own role schema instead of one blanket value.
        checked_components = {"table", "column", "primary_key", "foreign_key"}
        for table_name in ("person", "concept", "observation_period"):
            issues = [
                issue
                for issue in report.issues
                if issue.table_name == table_name and issue.component in checked_components
            ]
            assert issues == [], (table_name, issues)


def test_is_blocking_issue_excludes_renamed_only():
    from omop_alchemy.maintenance.cli_schema_reconcile import ReconciliationIssue
    from omop_alchemy.maintenance._cli_utils import Status
    from omop_alchemy.maintenance.tables import TableCategory

    renamed = ReconciliationIssue(
        table_name="person", category=TableCategory.CLINICAL, component="index",
        object_name=PERSON_GENDER_INDEX, status=Status.RENAMED,
        expected=PERSON_GENDER_INDEX, actual="idx_gender", detail="...",
    )
    missing = ReconciliationIssue(
        table_name="person", category=TableCategory.CLINICAL, component="index",
        object_name=PERSON_GENDER_INDEX, status=Status.MISSING,
        expected=PERSON_GENDER_INDEX, actual=None, detail="...",
    )
    assert is_blocking_issue(renamed) is False
    assert is_blocking_issue(missing) is True


def test_reconcile_schema_cluster_check_reports_renamed_for_foreign_cluster_index(fresh_reconcile_engine, monkeypatch):
    """A table physically clustered on a foreign-named equivalent of the ORM's
    cluster index (e.g. captured/restored under its original name by
    manage_indexes()) must report a 'renamed' cluster issue, not 'mismatch'."""
    engine = fresh_reconcile_engine
    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP INDEX {EPISODE_PERSON_INDEX}")
        connection.exec_driver_sql("CREATE INDEX idx_episode_person ON episode (person_id)")

    monkeypatch.setattr(
        SQLiteBackend,
        "get_clustered_index_name",
        lambda self, conn, table_name: (
            "idx_episode_person" if table_name == "episode" else None
        ),
    )

    report = reconcile_schema(engine)
    episode_result = next(r for r in report.table_results if r.table_name == "episode")
    cluster_issues = [
        issue for issue in report.issues
        if issue.table_name == "episode" and issue.component == "cluster"
    ]

    assert len(cluster_issues) == 1
    assert cluster_issues[0].status == "renamed"
    assert cluster_issues[0].expected == EPISODE_PERSON_INDEX
    assert cluster_issues[0].actual == "idx_episode_person"
    assert episode_result.status == "matched"


def test_reconcile_schema_cluster_check_still_reports_real_mismatch(fresh_reconcile_engine, monkeypatch):
    """A genuinely different physical cluster state (not just a foreign-named
    equivalent) must still be reported as drift."""
    engine = fresh_reconcile_engine

    monkeypatch.setattr(
        SQLiteBackend,
        "get_clustered_index_name",
        lambda self, conn, table_name: (
            "some_unrelated_index" if table_name == "episode" else None
        ),
    )

    report = reconcile_schema(engine)
    episode_result = next(r for r in report.table_results if r.table_name == "episode")
    cluster_issues = [
        issue for issue in report.issues
        if issue.table_name == "episode" and issue.component == "cluster"
    ]

    assert len(cluster_issues) == 1
    assert cluster_issues[0].status == "mismatch"
    assert episode_result.status == "drifted"


def test_reconcile_schema_cluster_check_reports_renamed_for_pk_based_cluster_target(fresh_reconcile_engine, monkeypatch):
    """person's cluster target is the primary key's own index ("pk_person"),
    not a declared secondary index, unlike episode. The official OHDSI CDM
    DDL always clusters such tables on a separate, non-unique index instead
    (e.g. "idx_person_id"). This must still report 'renamed', not
    'mismatch', and the same physical index must not also be flagged as an
    unexpected plain index. Both are the same latent bug: the cluster
    target's equivalence check assumes the PK's own uniqueness applies to
    whatever physically serves as the cluster index, and isn't shared with
    the general index-diffing pass."""
    engine = fresh_reconcile_engine
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE INDEX idx_person_id ON person (person_id)")

    monkeypatch.setattr(
        SQLiteBackend,
        "get_clustered_index_name",
        lambda self, conn, table_name: (
            "idx_person_id" if table_name == "person" else None
        ),
    )

    report = reconcile_schema(engine)
    person_result = next(r for r in report.table_results if r.table_name == "person")
    person_issues = [issue for issue in report.issues if issue.table_name == "person"]
    cluster_issues = [issue for issue in person_issues if issue.component == "cluster"]
    unexpected_index_issues = [
        issue for issue in person_issues
        if issue.component == "index" and issue.object_name == "idx_person_id"
    ]

    assert len(cluster_issues) == 1
    assert cluster_issues[0].status == "renamed"
    assert cluster_issues[0].expected == "pk_person"
    assert cluster_issues[0].actual == "idx_person_id"
    assert unexpected_index_issues == []
    assert person_result.status == "matched"
