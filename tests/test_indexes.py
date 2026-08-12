import pytest
import sqlalchemy as sa
from typer.testing import CliRunner
from oa_configurator import CDMDatabaseConfig, ConnectionConfig, StackConfig

from omop_alchemy.backends.sqlite import SQLiteBackend
from omop_alchemy.cdm.base.indexing import OMOP_CLUSTER_INDEX_INFO_KEY, omop_index_name
from omop_alchemy.maintenance.cli import app
from omop_alchemy.maintenance.cli_schema import create_missing_tables
from omop_alchemy.maintenance._cli_utils import ReservedSchema, Status, reject_reserved_schema
from omop_alchemy.maintenance.ui import render_index_summary
from omop_alchemy.maintenance.cli_indexes import (
    IndexManagementResult,
    _DROPPED_INDEXES_TABLE_NAME,
    get_bookkeeping_schema,
    _cluster_target_name,
    _describe_shape_conflict,
    _find_equivalent_index,
    _find_shape_conflict,
    _is_plain_index,
    _record_captured_index,
    _resolve_physical_cluster_name,
    _schema_metadata_indexes,
    collect_index_targets,
    manage_indexes,
)
from omop_alchemy.maintenance.tables import collect_maintenance_tables
from omop_alchemy.maintenance.tables import TableCategory
from omop_alchemy.maintenance.tables import select_omop_tables


runner = CliRunner()
PERSON_GENDER_INDEX = omop_index_name("person", "gender_concept_id")
CONCEPT_DOMAIN_INDEX = omop_index_name("concept", "domain_id")
EPISODE_PERSON_INDEX = omop_index_name("episode", "person_id")
CONCEPT_NAME_LOWER_INDEX = "ix_concept_concept_name_lower"
CONCEPT_SYNONYM_NAME_LOWER_INDEX = "ix_concept_synonym_concept_synonym_name_lower"


def _fresh_engine(tmp_path):
    db_path = tmp_path / "indexes.db"
    engine = sa.create_engine(f"sqlite:///{db_path}", future=True)
    create_missing_tables(engine)
    return engine


def test_collect_index_targets_excludes_vocabulary_by_default(tmp_path):
    """Test collect index targets excludes vocabulary by default."""
    engine = _fresh_engine(tmp_path)
    targets = {
        (target.table_name, target.index_name)
        for target in collect_index_targets(engine)
    }

    assert ("person", PERSON_GENDER_INDEX) in targets
    assert ("concept", CONCEPT_DOMAIN_INDEX) not in targets


@pytest.mark.filterwarnings(
    "ignore:Skipped unsupported reflection of expression-based index:sqlalchemy.exc.SAWarning"
)
def test_collect_index_targets_can_include_vocabulary(tmp_path):
    """Test collect index targets can include vocabulary.
    
    Notes
    -----
    collect_index_targets relies on SQLAlchemy's reflection, which cannot describe
    expression-based indexes on SQLite (e.g. concept.py ix_concept_concept_name_lower).
    The lower(concept_name) index is invisible to collect_index_targets even though it exists. 
    See test_manage_indexes_enable_is_idempotent_for_expression_indexes
    for coverage of the indexes themselves.
    
    """
    engine = _fresh_engine(tmp_path)
    targets = {
        (target.table_name, target.index_name)
        for target in collect_index_targets(engine, vocabulary_included=True)
    }

    assert ("concept", CONCEPT_DOMAIN_INDEX) in targets


def test_orm_index_metadata_carries_cluster_configuration():
    """Test orm index metadata carries cluster configuration."""
    tables = {
        table.table_name: table
        for table in collect_maintenance_tables()
    }

    person = tables["person"]
    assert person.table.info[OMOP_CLUSTER_INDEX_INFO_KEY] == "pk_person"

    episode = tables["episode"]
    episode_indexes = {
        index.name: index
        for index in episode.table.indexes
    }
    assert episode_indexes[EPISODE_PERSON_INDEX].info[OMOP_CLUSTER_INDEX_INFO_KEY] is True  # type: ignore[index]


def test_schema_metadata_indexes_keys_match_unadjusted_indexes():
    """Test schema metadata indexes keys match unadjusted indexes.

    manage_indexes() looks up schema-adjusted indexes using names taken from
    the original, unadjusted ORM tables. If a column's index name is resolved
    implicitly (e.g. via `index=True` rather than an explicit omop_index()),
    SQLAlchemy's naming convention embeds the schema into the generated name,
    so the schema-adjusted copy gets a different name and the lookup misses.
    """
    tables = select_omop_tables(vocabulary_included=True)
    indexes = _schema_metadata_indexes(tables, db_schema="public")

    for table in tables:
        for index in table.table.indexes:
            assert (table.table_name, str(index.name)) in indexes


def test_manage_indexes_disable_and_enable_on_sqlite(tmp_path):
    """Test manage indexes disable and enable on sqlite."""
    engine = _fresh_engine(tmp_path)

    inspector = sa.inspect(engine)
    before = {
        index["name"]
        for index in inspector.get_indexes("person")
    }
    assert PERSON_GENDER_INDEX in before

    disabled = manage_indexes(
        engine,
        enable=False,
    )
    assert disabled

    inspector = sa.inspect(engine)
    after_disable = {
        index["name"]
        for index in inspector.get_indexes("person")
    }
    assert PERSON_GENDER_INDEX not in after_disable

    enabled = manage_indexes(
        engine,
        enable=True,
    )
    assert enabled
    assert any(
        result.operation == "cluster" and result.status == "skipped"
        for result in enabled
    )
    assert any(
        "unsupported on SQLite" in result.detail
        for result in enabled
        if result.operation == "cluster"
    )

    inspector = sa.inspect(engine)
    after_enable = {
        index["name"]
        for index in inspector.get_indexes("person")
    }
    assert PERSON_GENDER_INDEX in after_enable


def test_manage_indexes_enable_analyzes_tables_with_new_indexes(tmp_path, monkeypatch):
    """Test manage indexes enable analyzes tables with new indexes."""
    engine = _fresh_engine(tmp_path)
    manage_indexes(engine, enable=False)

    analyzed_tables: list[str] = []
    original_analyze = SQLiteBackend.analyze_table

    def recording_analyze(self, conn, table_name, db_schema, *, vacuum=False):
        analyzed_tables.append(table_name)
        return original_analyze(self, conn, table_name, db_schema, vacuum=vacuum)

    monkeypatch.setattr(SQLiteBackend, "analyze_table", recording_analyze)

    manage_indexes(engine, enable=True)

    assert "person" in analyzed_tables


def test_manage_indexes_enable_skips_analyze_when_nothing_created(tmp_path, monkeypatch):
    """Test manage indexes enable skips analyze when nothing created."""
    engine = _fresh_engine(tmp_path)

    analyzed_tables: list[str] = []
    monkeypatch.setattr(
        SQLiteBackend,
        "analyze_table",
        lambda self, conn, table_name, db_schema, *, vacuum=False: analyzed_tables.append(table_name),
    )

    # All ORM-defined indexes already exist on a freshly created schema, so
    # enabling again should be a no-op and must not trigger any ANALYZE calls.
    manage_indexes(engine, enable=True)

    assert analyzed_tables == []


@pytest.mark.filterwarnings(
    "ignore:Skipped unsupported reflection of expression-based index:sqlalchemy.exc.SAWarning"
)
def test_manage_indexes_enable_is_idempotent_for_expression_indexes(tmp_path):
    """Test manage indexes enable is idempotent for expression indexes.

    SQLite cannot reflect expression-based indexes (e.g. lower(concept_name)),
    so manage_indexes can never see them as already existing via
    inspector.get_indexes(). Re-running 'enable' must not crash on the
    resulting duplicate-create attempt and must report it as skipped rather
    than falsely claiming the index was (re)created.
    """
    engine = _fresh_engine(tmp_path)

    for _ in range(2):
        results = manage_indexes(engine, enable=True, vocabulary_included=True)
        lower_index_results = {
            result.index_name: result
            for result in results
            if result.index_name in (CONCEPT_NAME_LOWER_INDEX, CONCEPT_SYNONYM_NAME_LOWER_INDEX)
        }
        assert set(lower_index_results) == {
            CONCEPT_NAME_LOWER_INDEX,
            CONCEPT_SYNONYM_NAME_LOWER_INDEX,
        }
        for result in lower_index_results.values():
            assert result.status == "skipped"
            assert "already exists" in result.detail


@pytest.mark.filterwarnings(
    "ignore:Skipped unsupported reflection of expression-based index:sqlalchemy.exc.SAWarning"
)
def test_manage_indexes_disable_drops_expression_indexes_on_sqlite(tmp_path):
    """Test manage indexes disable drops expression indexes on sqlite.

    SQLite can't reflect expression-based indexes, so `disable` must not gate
    the drop on inspector.get_indexes() (`exists` would always be False) and
    must not rely on Index.drop(checkfirst=True) either, since checkfirst
    does its own reflection-based check internally and would silently no-op
    the same way. The index must actually be removed, and a result row must
    always be reported.
    """
    engine = _fresh_engine(tmp_path)

    def _index_exists(name: str) -> bool:
        with engine.connect() as connection:
            row = connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
                (name,),
            ).fetchone()
        return row is not None

    assert _index_exists(CONCEPT_NAME_LOWER_INDEX)
    assert _index_exists(CONCEPT_SYNONYM_NAME_LOWER_INDEX)

    results = manage_indexes(engine, enable=False, vocabulary_included=True)
    lower_index_results = {
        result.index_name: result
        for result in results
        if result.index_name in (CONCEPT_NAME_LOWER_INDEX, CONCEPT_SYNONYM_NAME_LOWER_INDEX)
    }
    assert set(lower_index_results) == {
        CONCEPT_NAME_LOWER_INDEX,
        CONCEPT_SYNONYM_NAME_LOWER_INDEX,
    }
    for result in lower_index_results.values():
        assert result.status == "applied"

    assert not _index_exists(CONCEPT_NAME_LOWER_INDEX)
    assert not _index_exists(CONCEPT_SYNONYM_NAME_LOWER_INDEX)


@pytest.mark.filterwarnings(
    "ignore:Skipped unsupported reflection of expression-based index:sqlalchemy.exc.SAWarning"
)
def test_manage_indexes_disable_is_idempotent_on_sqlite(tmp_path):
    """A second disable run should report already-absent indexes as skipped."""
    engine = _fresh_engine(tmp_path)

    first_results = manage_indexes(engine, enable=False, vocabulary_included=True)
    second_results = manage_indexes(engine, enable=False, vocabulary_included=True)

    first_lower = {
        result.index_name: result
        for result in first_results
        if result.index_name in (CONCEPT_NAME_LOWER_INDEX, CONCEPT_SYNONYM_NAME_LOWER_INDEX)
    }
    second_lower = {
        result.index_name: result
        for result in second_results
        if result.index_name in (CONCEPT_NAME_LOWER_INDEX, CONCEPT_SYNONYM_NAME_LOWER_INDEX)
    }

    for result in first_lower.values():
        assert result.status == "applied"

    for result in second_lower.values():
        assert result.status == "skipped"
        assert "already absent" in result.detail


def test_manage_indexes_enable_clusters_then_analyzes(tmp_path, monkeypatch):
    """Test manage indexes enable clusters then analyzes, even with nothing created.

    `indexes enable --cluster` must ANALYZE a table whenever it was clustered,
    not only when a new index was created (a table can be selected for
    clustering with all its indexes already in place), and must do so *after*
    clustering so planner stats reflect the final physical layout -- matching
    the standalone `indexes cluster` command's order.
    """
    engine = _fresh_engine(tmp_path)

    calls: list[str] = []

    def fake_cluster_table(self, conn, table_name, index_name, db_schema):
        calls.append(f"cluster:{table_name}")

    def fake_analyze_table(self, conn, table_name, db_schema, *, vacuum=False):
        calls.append(f"analyze:{table_name}")

    monkeypatch.setattr(SQLiteBackend, "cluster_table", fake_cluster_table)
    monkeypatch.setattr(SQLiteBackend, "analyze_table", fake_analyze_table)

    # All ORM-defined indexes already exist on a freshly created schema, so
    # `created_any` stays False -- only the cluster step causes any change.
    manage_indexes(engine, enable=True, cluster=True)

    assert "cluster:person" in calls
    assert "analyze:person" in calls
    assert calls.index("cluster:person") < calls.index("analyze:person")


def test_disable_indexes_cli_invokes_management(monkeypatch):
    """Test disable indexes cli invokes management."""

    calls: dict[str, object] = {}

    cfg = StackConfig.for_session(
        connections={"db": ConnectionConfig(dialect="sqlite", database_name=":memory:")},
        databases={"cdm_db": CDMDatabaseConfig(connection="db", schema_name="main")},
    )
    monkeypatch.setattr(
        "omop_alchemy.config.load_stack_config",
        lambda: cfg,
    )

    def fake_manage_indexes(
        engine: object,
        *,
        enable: bool,
        db_schema: str | None = None,
        vocabulary_included: bool = False,
        dry_run: bool = False,
    ) -> list[IndexManagementResult]:
        calls["engine"] = engine
        calls["enable"] = enable
        calls["db_schema"] = db_schema
        calls["vocabulary_included"] = vocabulary_included
        calls["dry_run"] = dry_run
        return [
            IndexManagementResult(
                operation="index",
                table_name="person",
                category=TableCategory.CLINICAL,
                index_name=PERSON_GENDER_INDEX,
                column_names=("gender_concept_id",),
                unique=False,
                clustered=False,
                enable=enable,
                status=Status.PLANNED,
                detail="metadata-defined index would be dropped",
            )
        ]

    monkeypatch.setattr(
        "omop_alchemy.maintenance.cli_indexes.manage_indexes",
        fake_manage_indexes,
    )

    result = runner.invoke(
        app,
        [
            "indexes",
            "disable",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "indexes disable" in result.stdout
    assert "person" in result.stdout
    assert "disable" in result.stdout
    assert "PLANNED" in result.stdout
    assert "Planned disable on 1 metadata operation(s)." in result.stdout


def test_enable_indexes_cli_no_cluster_flag_passes_through(monkeypatch):
    """Test enable indexes cli no cluster flag passes through."""

    calls: dict[str, object] = {}

    cfg = StackConfig.for_session(
        connections={"db": ConnectionConfig(dialect="sqlite", database_name=":memory:")},
        databases={"cdm_db": CDMDatabaseConfig(connection="db", schema_name="main")},
    )
    monkeypatch.setattr(
        "omop_alchemy.config.load_stack_config",
        lambda: cfg,
    )

    def fake_manage_indexes(
        engine: object,
        *,
        enable: bool,
        db_schema: str | None = None,
        vocabulary_included: bool = False,
        dry_run: bool = False,
        cluster: bool = True,
    ) -> list[IndexManagementResult]:
        calls["enable"] = enable
        calls["vocabulary_included"] = vocabulary_included
        calls["dry_run"] = dry_run
        calls["cluster"] = cluster
        return [
            IndexManagementResult(
                operation="index",
                table_name="person",
                category=TableCategory.CLINICAL,
                index_name=PERSON_GENDER_INDEX,
                column_names=("gender_concept_id",),
                unique=False,
                clustered=False,
                enable=enable,
                status=Status.PLANNED,
                detail="metadata-defined index would be created",
            )
        ]

    monkeypatch.setattr(
        "omop_alchemy.maintenance.cli_indexes.manage_indexes",
        fake_manage_indexes,
    )

    result = runner.invoke(
        app,
        [
            "indexes",
            "enable",
            "--no-cluster",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert calls["cluster"] is False
    assert calls["enable"] is True
    assert calls["dry_run"] is True


# ── Column-set equivalence helpers ──────────────────────────────────────────────

_PLAIN = {"name": "idx_person_id", "column_names": ["person_id"], "unique": False}
_EXPRESSION = {
    "name": "idx_lower",
    "column_names": [None],
    "expressions": ["lower(x)"],
    "unique": False,
}
_PARTIAL = {
    "name": "idx_partial",
    "column_names": ["gender_concept_id"],
    "unique": False,
    "dialect_options": {"postgresql_where": "gender_concept_id IS NOT NULL"},
}
_NON_BTREE = {
    "name": "idx_gin",
    "column_names": ["x"],
    "unique": False,
    "dialect_options": {"postgresql_using": "gin"},
}
_UNIQUE_PLAIN = {"name": "uq_person_id", "column_names": ["person_id"], "unique": True}


def test_is_plain_index_true_for_ordinary_reflected_index():
    assert _is_plain_index(_PLAIN) is True


def test_is_plain_index_false_for_expression_index():
    assert _is_plain_index(_EXPRESSION) is False


def test_is_plain_index_false_for_partial_index():
    assert _is_plain_index(_PARTIAL) is False


def test_is_plain_index_false_for_non_btree_index():
    assert _is_plain_index(_NON_BTREE) is False


def test_find_equivalent_index_is_order_sensitive():
    existing = [{"name": "idx_ab", "column_names": ["b", "a"], "unique": False}]
    assert _find_equivalent_index(existing, ("a", "b"), False) is None
    existing = [{"name": "idx_ab", "column_names": ["a", "b"], "unique": False}]
    assert _find_equivalent_index(existing, ("a", "b"), False) == "idx_ab"


def test_find_equivalent_index_respects_unique_flag():
    existing = [_UNIQUE_PLAIN]
    assert _find_equivalent_index(existing, ("person_id",), False) is None
    assert _find_equivalent_index(existing, ("person_id",), True) == "uq_person_id"


def test_find_equivalent_index_ignores_non_plain_matches():
    existing = [_PARTIAL]
    assert _find_equivalent_index(existing, ("gender_concept_id",), False) is None


def test_find_shape_conflict_detects_partial_and_non_btree_but_not_plain():
    existing = [_PLAIN, _PARTIAL, _NON_BTREE]
    assert _find_shape_conflict(existing, ("person_id",), False) is None
    assert _find_shape_conflict(existing, ("gender_concept_id",), False) is _PARTIAL
    assert _find_shape_conflict(existing, ("x",), False) is _NON_BTREE


def test_describe_shape_conflict_mentions_reason():
    assert "partial WHERE predicate" in _describe_shape_conflict(_PARTIAL)
    assert "non-btree access method 'gin'" in _describe_shape_conflict(_NON_BTREE)


# ── Reserved schema guard ────────────────────────────────────────────────────────


def test_reject_reserved_schema_rejects_staging_and_maintenance():
    with pytest.raises(RuntimeError):
        reject_reserved_schema(ReservedSchema.STAGING.value)
    with pytest.raises(RuntimeError):
        reject_reserved_schema(ReservedSchema.MAINTENANCE.value)


def test_reject_reserved_schema_allows_ordinary_schema():
    reject_reserved_schema("public")
    reject_reserved_schema(None)


# ── Foreign-named equivalent index reconciliation ────────────────────────────────


def _replace_with_foreign_index(engine: sa.Engine, *, foreign_name: str) -> None:
    """Simulate a prepopulated database indexed by the official OHDSI CDM DDL
    script: drop OMOP_Alchemy's own person.gender_concept_id index and create a
    plain, differently-named index covering the same column instead."""
    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP INDEX {PERSON_GENDER_INDEX}")
        connection.exec_driver_sql(
            f"CREATE INDEX {foreign_name} ON person (gender_concept_id)"
        )


def _person_gender_result(results: list[IndexManagementResult]) -> IndexManagementResult:
    matches = [
        result
        for result in results
        if result.table_name == "person"
        and result.operation == "index"
        and result.column_names == ("gender_concept_id",)
    ]
    assert len(matches) == 1
    return matches[0]


def test_collect_index_targets_reports_foreign_named_equivalent_index(tmp_path):
    engine = _fresh_engine(tmp_path)
    _replace_with_foreign_index(engine, foreign_name="idx_gender")

    targets = {
        (target.table_name, target.index_name)
        for target in collect_index_targets(engine)
    }

    assert ("person", "idx_gender") in targets
    assert ("person", PERSON_GENDER_INDEX) not in targets


def test_manage_indexes_enable_skips_creation_when_foreign_equivalent_exists(tmp_path):
    engine = _fresh_engine(tmp_path)
    _replace_with_foreign_index(engine, foreign_name="idx_gender")

    results = manage_indexes(engine, enable=True, cluster=False)
    result = _person_gender_result(results)

    assert result.status == "skipped"
    assert "idx_gender" in result.detail
    assert result.index_name == "idx_gender"

    inspector = sa.inspect(engine)
    index_names = {index["name"] for index in inspector.get_indexes("person")}
    assert "idx_gender" in index_names
    assert PERSON_GENDER_INDEX not in index_names


def test_manage_indexes_disable_captures_and_drops_foreign_equivalent_index(tmp_path):
    engine = _fresh_engine(tmp_path)
    _replace_with_foreign_index(engine, foreign_name="idx_gender")

    results = manage_indexes(engine, enable=False)
    result = _person_gender_result(results)

    assert result.status == "captured"
    assert result.index_name == "idx_gender"

    inspector = sa.inspect(engine)
    index_names = {index["name"] for index in inspector.get_indexes("person")}
    assert "idx_gender" not in index_names

    bookkeeping = _dropped_indexes_rows(engine)
    assert len(bookkeeping) == 1
    assert bookkeeping[0]["table_name"] == "person"
    assert bookkeeping[0]["index_name"] == "idx_gender"


def test_manage_indexes_enable_restores_captured_foreign_index(tmp_path):
    engine = _fresh_engine(tmp_path)
    _replace_with_foreign_index(engine, foreign_name="idx_gender")

    manage_indexes(engine, enable=False)

    # A second, independent manage_indexes() call -- simulating a fresh CLI
    # process -- must still be able to restore, since the capture lives in the
    # database rather than in process memory.
    results = manage_indexes(engine, enable=True, cluster=False)
    result = _person_gender_result(results)

    assert result.status == "restored"
    assert result.index_name == "idx_gender"

    inspector = sa.inspect(engine)
    index_names = {index["name"] for index in inspector.get_indexes("person")}
    assert "idx_gender" in index_names
    assert PERSON_GENDER_INDEX not in index_names

    assert _dropped_indexes_rows(engine) == []


def test_manage_indexes_disable_enable_round_trip_is_idempotent_with_capture(tmp_path):
    engine = _fresh_engine(tmp_path)
    _replace_with_foreign_index(engine, foreign_name="idx_gender")

    for _ in range(2):
        manage_indexes(engine, enable=False)
        manage_indexes(engine, enable=True, cluster=False)

    inspector = sa.inspect(engine)
    index_names = [index["name"] for index in inspector.get_indexes("person")]
    assert index_names.count("idx_gender") == 1
    assert _dropped_indexes_rows(engine) == []


def test_manage_indexes_dry_run_previews_foreign_equivalent_without_mutating(tmp_path):
    engine = _fresh_engine(tmp_path)
    _replace_with_foreign_index(engine, foreign_name="idx_gender")

    results = manage_indexes(engine, enable=True, dry_run=True, cluster=False)
    result = _person_gender_result(results)

    assert result.status == "skipped"
    assert "idx_gender" in result.detail

    inspector = sa.inspect(engine)
    assert not inspector.has_table(
        _DROPPED_INDEXES_TABLE_NAME, schema=get_bookkeeping_schema(SQLiteBackend())
    )


def test_bookkeeping_table_not_created_when_nothing_to_capture(tmp_path):
    engine = _fresh_engine(tmp_path)

    manage_indexes(engine, enable=False)
    manage_indexes(engine, enable=True, cluster=False)

    inspector = sa.inspect(engine)
    assert not inspector.has_table(
        _DROPPED_INDEXES_TABLE_NAME, schema=get_bookkeeping_schema(SQLiteBackend())
    )


def test_manage_indexes_enable_cluster_uses_restored_physical_name(tmp_path, monkeypatch):
    engine = _fresh_engine(tmp_path)

    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP INDEX {EPISODE_PERSON_INDEX}")
        connection.exec_driver_sql(
            "CREATE INDEX idx_episode_person_id_1 ON episode (person_id)"
        )

    manage_indexes(engine, enable=False)

    calls: list[tuple[str, str]] = []

    def fake_cluster_table(self, conn, table_name, index_name, db_schema):
        calls.append((table_name, index_name))

    monkeypatch.setattr(SQLiteBackend, "cluster_table", fake_cluster_table)
    monkeypatch.setattr(
        SQLiteBackend,
        "analyze_table",
        lambda self, conn, table_name, db_schema, *, vacuum=False: None,
    )

    manage_indexes(engine, enable=True, cluster=True)

    assert ("episode", "idx_episode_person_id_1") in calls


def test_manage_indexes_disable_warns_and_leaves_unsupported_foreign_index_in_place(
    tmp_path, monkeypatch
):
    engine = _fresh_engine(tmp_path)

    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP INDEX {PERSON_GENDER_INDEX}")
        connection.exec_driver_sql(
            "CREATE INDEX idx_gender_partial ON person (gender_concept_id)"
        )

    original_get_indexes = sa.Inspector.get_indexes

    def fake_get_indexes(self, table_name, schema=None, **kw):
        reflected = list(original_get_indexes(self, table_name, schema=schema, **kw))
        if table_name == "person":
            for index in reflected:
                if index["name"] == "idx_gender_partial":
                    index["dialect_options"] = {
                        "postgresql_where": "gender_concept_id IS NOT NULL"
                    }
        return reflected

    monkeypatch.setattr(sa.Inspector, "get_indexes", fake_get_indexes)

    results = manage_indexes(engine, enable=False)
    result = _person_gender_result(results)

    assert result.status == "warning"
    assert "idx_gender_partial" in result.detail
    assert "partial WHERE predicate" in result.detail

    inspector = sa.inspect(engine)
    index_names = {index["name"] for index in inspector.get_indexes("person")}
    assert "idx_gender_partial" in index_names
    assert _dropped_indexes_rows(engine) == []

    # The rest of the run must complete normally -- other tables' results are unaffected.
    other_results = [r for r in results if r.table_name != "person"]
    assert other_results


def _dropped_indexes_rows(engine: sa.Engine) -> list[dict[str, object]]:
    bookkeeping_schema = get_bookkeeping_schema(SQLiteBackend())
    inspector = sa.inspect(engine)
    if not inspector.has_table(_DROPPED_INDEXES_TABLE_NAME, schema=bookkeeping_schema):
        return []
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            f"SELECT table_name, index_name FROM {_DROPPED_INDEXES_TABLE_NAME}"
        ).mappings().all()
    return [dict(row) for row in rows]


def _render_to_text(renderable) -> str:
    from rich.console import Console
    import io

    buffer = io.StringIO()
    Console(file=buffer, width=200).print(renderable)
    return buffer.getvalue()


def _warning_result() -> IndexManagementResult:
    return IndexManagementResult(
        operation="index",
        table_name="person",
        category=TableCategory.CLINICAL,
        index_name="idx_gender_partial",
        column_names=("gender_concept_id",),
        unique=False,
        clustered=False,
        enable=False,
        status=Status.WARNING,
        detail="foreign index 'idx_gender_partial' has a partial WHERE predicate; left in place",
    )


def test_render_index_summary_reports_warning_count():
    text = _render_to_text(render_index_summary([_warning_result()], dry_run=False))
    assert "Warnings" in text
    assert "1" in text


def test_render_index_summary_omits_warnings_row_when_none():
    ok_result = IndexManagementResult(
        operation="index",
        table_name="person",
        category=TableCategory.CLINICAL,
        index_name=PERSON_GENDER_INDEX,
        column_names=("gender_concept_id",),
        unique=False,
        clustered=False,
        enable=True,
        status=Status.APPLIED,
        detail="metadata-defined index created",
    )
    text = _render_to_text(render_index_summary([ok_result], dry_run=False))
    assert "Warnings" not in text


# ── Review-round-2 regression tests ──────────────────────────────────────────────


def test_is_plain_index_false_for_constraint_backed_index():
    """A foreign index reflecting with duplicates_constraint set backs a UNIQUE/
    PRIMARY KEY constraint. PostgreSQL refuses DROP INDEX on those, so it must
    never be treated as a safe capture-and-drop equivalent."""
    constraint_backed = {
        "name": "person_pkey",
        "column_names": ["person_id"],
        "unique": True,
        "duplicates_constraint": "person_pkey",
    }
    assert _is_plain_index(constraint_backed) is False
    assert _find_equivalent_index([constraint_backed], ("person_id",), True) is None
    assert _find_shape_conflict([constraint_backed], ("person_id",), True) is constraint_backed
    assert "constraint" in _describe_shape_conflict(constraint_backed)


def test_manage_indexes_disable_second_run_without_enable_degrades_to_warning(tmp_path):
    """A second disable run, without an intervening enable, that finds a
    different foreign index than the one already captured must not crash.
    It must leave the second index in place and report a warning, since the
    bookkeeping table can only track one pending capture per table/column-set."""
    engine = _fresh_engine(tmp_path)
    _replace_with_foreign_index(engine, foreign_name="idx_gender_v1")

    first = manage_indexes(engine, enable=False)
    assert _person_gender_result(first).status == "captured"

    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE INDEX idx_gender_v2 ON person (gender_concept_id)")

    second = manage_indexes(engine, enable=False)
    result = _person_gender_result(second)
    assert result.status == "warning"
    assert "idx_gender_v2" in result.detail

    inspector = sa.inspect(engine)
    index_names = {index["name"] for index in inspector.get_indexes("person")}
    assert "idx_gender_v2" in index_names, "the untrackable second foreign index must be left in place"

    # The originally captured index (idx_gender_v1) must still be restorable.
    third = manage_indexes(engine, enable=True, cluster=False)
    restored = _person_gender_result(third)
    assert restored.status == "restored"
    assert restored.index_name == "idx_gender_v1"


def test_record_captured_index_scopes_by_db_schema(tmp_path):
    """Two different schemas capturing an equivalent foreign index for a
    same-named table/column-set must not collide. Each capture is independent
    and neither should be rejected by the other's bookkeeping row."""
    engine = _fresh_engine(tmp_path)
    backend = SQLiteBackend()

    with engine.begin() as connection:
        captured_a = _record_captured_index(
            connection, backend,
            table_name="person", db_schema="site_a", index_name="idx_a",
            column_names=("gender_concept_id",), unique=False,
        )
        captured_b = _record_captured_index(
            connection, backend,
            table_name="person", db_schema="site_b", index_name="idx_b",
            column_names=("gender_concept_id",), unique=False,
        )

    assert captured_a is True
    assert captured_b is True

    # Capturing again for the *same* schema, with a different foreign name, must
    # still be rejected (this is the case test_manage_indexes_disable_second_run_
    # without_enable_degrades_to_warning covers end-to-end).
    with engine.begin() as connection:
        captured_a_again = _record_captured_index(
            connection, backend,
            table_name="person", db_schema="site_a", index_name="idx_a_v2",
            column_names=("gender_concept_id",), unique=False,
        )
    assert captured_a_again is False


def test_resolve_physical_cluster_name_prefers_own_name():
    existing = [{"name": "ix_person_gender_concept_id", "column_names": ["gender_concept_id"], "unique": False}]
    assert (
        _resolve_physical_cluster_name(existing, "ix_person_gender_concept_id", ("gender_concept_id",))
        == "ix_person_gender_concept_id"
    )


def test_resolve_physical_cluster_name_falls_back_to_equivalent():
    existing = [{"name": "idx_gender", "column_names": ["gender_concept_id"], "unique": False}]
    assert (
        _resolve_physical_cluster_name(existing, "ix_person_gender_concept_id", ("gender_concept_id",))
        == "idx_gender"
    )


def test_resolve_physical_cluster_name_falls_back_to_own_name_when_nothing_matches():
    assert _resolve_physical_cluster_name([], "ix_person_gender_concept_id", ("gender_concept_id",)) == (
        "ix_person_gender_concept_id"
    )


def test_resolve_physical_cluster_name_ignores_uniqueness_for_pk_based_target():
    """A primary-key-based cluster target (e.g. "pk_person") is always unique,
    but the official OHDSI CDM DDL always clusters such tables on a separate,
    non-unique index instead (e.g. "idx_person_id"). Equivalence must not
    require a uniqueness match, or exactly this real-world case is missed."""
    existing = [{"name": "idx_person_id", "column_names": ["person_id"], "unique": False}]
    assert _resolve_physical_cluster_name(existing, "pk_person", ("person_id",)) == "idx_person_id"


def test_vocabulary_domain_concept_class_relationship_cluster_on_primary_key():
    """vocabulary/domain/concept_class/relationship previously declared a
    redundant secondary index as their cluster target (same column as their own
    primary key), inconsistently with person/location/care_site/provider/concept
    which cluster directly on the primary key's own index. Normalized onto the
    latter: Alchemy never creates that redundant index itself, and index
    reconciliation is what recognizes a database that has the OHDSI-standard
    duplicate (see _resolve_physical_cluster_name_falls_back_to_equivalent)
    as an equivalent cluster target."""
    tables = {table.table_name: table for table in collect_maintenance_tables()}
    for table_name, pk_column in (
        ("vocabulary", "vocabulary_id"),
        ("domain", "domain_id"),
        ("concept_class", "concept_class_id"),
        ("relationship", "relationship_id"),
    ):
        table = tables[table_name]
        cluster_name = _cluster_target_name(table)
        assert cluster_name == f"pk_{table_name}"
        assert not any(
            set(index.columns.keys()) == {pk_column}
            for index in table.table.indexes
        )


def test_render_reconciliation_summary_treats_renamed_only_as_matched():
    from omop_alchemy.maintenance.cli_schema_reconcile import ReconciliationIssue, SchemaReconciliationReport, TableReconciliationResult
    from omop_alchemy.maintenance._cli_utils import Status
    from omop_alchemy.maintenance.ui import render_reconciliation_summary

    report = SchemaReconciliationReport(
        backend="sqlite",
        table_results=(
            TableReconciliationResult(
                table_name="person",
                category=TableCategory.CLINICAL,
                status=Status.MATCHED,
                issue_count=1,
                detail="1 difference(s) detected.",
            ),
        ),
        issues=(
            ReconciliationIssue(
                table_name="person",
                category=TableCategory.CLINICAL,
                component="index",
                object_name=PERSON_GENDER_INDEX,
                status=Status.RENAMED,
                expected=PERSON_GENDER_INDEX,
                actual="idx_gender",
                detail="Index is present under a different name.",
            ),
        ),
    )

    text = _render_to_text(render_reconciliation_summary(report))
    assert "matches ORM metadata" in text
    assert "drift detected" not in text.lower()
    assert "Renamed" in text
