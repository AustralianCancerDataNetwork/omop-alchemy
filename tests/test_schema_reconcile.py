import pytest

from oa_configurator.testing import DIALECT_PARAMS
from omop_alchemy.backends.sqlite import SQLiteBackend
from omop_alchemy.cdm.base.indexing import omop_index_name
from omop_alchemy.maintenance.cli_indexes import manage_indexes
from omop_alchemy.maintenance.cli_schema import create_missing_tables
from omop_alchemy.maintenance.cli_schema_reconcile import is_blocking_issue, reconcile_schema

PERSON_GENDER_INDEX = omop_index_name("person", "gender_concept_id")
EPISODE_PERSON_INDEX = omop_index_name("episode", "person_id")


@pytest.fixture(params=DIALECT_PARAMS)
def reconcile_engine(request):
    """Every OMOP table created and indexed/clustered, on both a real
    Postgres backend and SQLite: index-rename detection is dialect-portable
    logic, not SQLite-specific, so it's genuinely worth exercising against
    both. Only the postgresql param ever requests pg_session, so the
    sqlite param never needs a database.

    DIALECT_PARAMS carries each dialect's own mark plus `forked` directly
    on the param value, so this still works correctly even though
    request.getfixturevalue("pg_session") is a dynamic, runtime lookup
    invisible to pytest's collection-time fixturenames computation (the
    usual pg_db-in-fixturenames auto-detection can't see it).

    manage_indexes(enable=True) matters here specifically on Postgres:
    create_missing_tables() alone creates tables and their indexes, but
    never physically CLUSTERs them, so a fresh Postgres database reports
    genuine drift (cluster status MISSING) without this step. On SQLite,
    where CLUSTER doesn't exist, this call is a harmless no-op on the
    clustering half and just re-confirms indexes already exist.
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
    (e.g. "idx_person_id"): this must still report 'renamed', not 'mismatch',
    and the same physical index must not *also* be flagged as an unexpected
    plain index -- both are the same latent bug (the cluster target's
    equivalence check assuming the PK's own uniqueness applies to whatever
    physically serves as the cluster index, and not being shared with the
    general index-diffing pass)."""
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
