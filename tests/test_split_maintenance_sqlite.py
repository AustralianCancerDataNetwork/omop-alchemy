"""Maintenance routing against two real SQLite files."""

from __future__ import annotations

import sqlalchemy as sa

from omop_alchemy.maintenance.cli_schema import _create_missing_tables, collect_data_summary
from omop_alchemy.maintenance.cli_schema_reconcile import reconcile_schema
from omop_alchemy.maintenance.cli_tables import _truncate_tables, analyze_tables
from omop_alchemy.maintenance.tables import TableCategory
from omop_alchemy.maintenance.cli_indexes import collect_index_targets
from omop_alchemy.maintenance.cli_fulltext import _fulltext_groups
from omop_alchemy.backends.base import FullTextTargetConfig
from omop_alchemy.maintenance.cli_foreign_keys import (
    collect_foreign_key_trigger_status,
    manage_foreign_key_triggers,
)


def _create_split_tables(context):
    _create_missing_tables(context, vocabulary_included=True)


def _assert_vocab_only(split_sqlite, table_name: str) -> None:
    assert sa.inspect(split_sqlite.vocab).has_table(table_name)
    assert not sa.inspect(split_sqlite.primary).has_table(table_name)


def test_split_analyze_targets_vocabulary_file(sqlite_split):
    _create_split_tables(sqlite_split.context)

    results = analyze_tables(
        sqlite_split.context, scope=TableCategory.VOCABULARY
    )

    assert any(result.table_name == "concept" for result in results)
    _assert_vocab_only(sqlite_split, "concept")


def test_split_truncate_plans_vocabulary_file(sqlite_split, monkeypatch):
    _create_split_tables(sqlite_split.context)
    monkeypatch.setattr(
        "omop_alchemy.maintenance.cli_tables.require_backend_support",
        lambda *args, **kwargs: None,
    )

    results = _truncate_tables(
        sqlite_split.context,
        scope=TableCategory.VOCABULARY,
        cascade=True,
        dry_run=True,
    )

    assert any(result.table_name == "concept" for result in results)
    _assert_vocab_only(sqlite_split, "concept")


def test_split_fulltext_targets_group_on_vocab_engine(sqlite_split, monkeypatch):
    class FakeBackend:
        fulltext_targets = (
            FullTextTargetConfig(
                table_name="concept",
                source_column_name="concept_name",
                vector_column_name="concept_name_tsvector",
                index_name="idx_concept_name_tsvector",
            ),
        )

    monkeypatch.setattr(
        "omop_alchemy.maintenance.cli_fulltext.require_backend_support",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "omop_alchemy.maintenance.cli_fulltext.resolve_backend",
        lambda engine: FakeBackend(),
    )

    groups = _fulltext_groups(sqlite_split.context, "install_fulltext_on_table")

    assert groups
    assert all(engine is sqlite_split.vocab for engine, _, _ in groups)
    assert "concept" in {target.table_name for _, _, targets in groups for target in targets}
    assert not sa.inspect(sqlite_split.primary).has_table("concept")


def test_split_fk_status_and_toggle_use_vocab_engine(sqlite_split, monkeypatch):
    _create_split_tables(sqlite_split.context)

    class FakeBackend:
        def toggle_fk_triggers(self, *args, **kwargs):
            raise AssertionError("dry run must not toggle SQLite triggers")

        def get_fk_trigger_counts(self, *args, **kwargs):
            return 0, 0

    monkeypatch.setattr(
        "omop_alchemy.maintenance.cli_foreign_keys._backends",
        lambda context, capability, label: {engine: FakeBackend() for engine in context.engines},
    )
    toggles = manage_foreign_key_triggers(
        sqlite_split.context,
        enable=False,
        vocabulary_only=True,
        dry_run=True,
    )
    status = collect_foreign_key_trigger_status(
        sqlite_split.context, vocabulary_included=True
    )

    assert any(result.table_name == "concept" for result in toggles)
    assert any(result.table_name == "concept" for result in status)
    _assert_vocab_only(sqlite_split, "concept")


def test_split_reconcile_inspects_vocabulary_file(sqlite_split):
    _create_split_tables(sqlite_split.context)

    report = reconcile_schema(sqlite_split.context, vocabulary_included=True)

    assert any(result.table_name == "concept" for result in report.table_results)
    _assert_vocab_only(sqlite_split, "concept")


def test_split_data_summary_reads_vocabulary_rows(sqlite_split):
    _create_split_tables(sqlite_split.context)

    summary = {
        result.table_name: result
        for result in collect_data_summary(
            sqlite_split.context, vocabulary_included=True
        )
    }

    assert summary["concept"].exists
    assert summary["concept"].row_count == 0
    _assert_vocab_only(sqlite_split, "concept")


def test_split_index_targets_inspect_vocabulary_file(sqlite_split):
    _create_split_tables(sqlite_split.context)

    targets = collect_index_targets(
        sqlite_split.context, vocabulary_included=True
    )

    assert any(target.table_name == "concept" for target in targets)
    _assert_vocab_only(sqlite_split, "concept")


def test_split_load_vocab_source_writes_only_vocabulary_file(
    sqlite_split, athena_source_dir
):
    from omop_alchemy.maintenance.cli_vocab import load_vocab_source

    load_vocab_source(
        sqlite_split.context,
        source_path=athena_source_dir,
        bulk_mode=False,
    )

    _assert_vocab_only(sqlite_split, "concept")
    with sqlite_split.vocab.connect() as connection:
        assert connection.execute(sa.text("SELECT COUNT(*) FROM concept")).scalar_one() > 0
