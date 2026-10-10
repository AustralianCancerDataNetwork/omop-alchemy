"""PostgreSQL split-database coverage for maintenance commands."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from omop_alchemy.maintenance.cli_foreign_keys import (
    collect_foreign_key_trigger_status,
    manage_foreign_key_triggers,
)
from omop_alchemy.maintenance.cli_fulltext import (
    _install_fulltext_columns,
    drop_fulltext_columns,
    populate_fulltext_columns,
)
from omop_alchemy.maintenance.cli_tables import _truncate_tables
from omop_alchemy.maintenance.tables import TableCategory
from omop_alchemy.maintenance.context import MaintenanceContext
from omop_alchemy.config import MAINTENANCE_SCHEMA
from oa_configurator import SCHEMA_REGISTRY_SCHEMA
from omop_alchemy.maintenance.cli_backup import BackupFormat, _single_connection_backup


pytestmark = [pytest.mark.postgresql, pytest.mark.db_dialect]


def test_backup_includes_maintenance_schema(pg_engine, pg_resolved, monkeypatch, tmp_path):
    captured = {}

    class FakeBackend:
        def prepare_backup(self, engine, path, backup_format, *, schemas):
            captured["schemas"] = schemas
            return "pg_dump", [], {}, engine.url.database

    monkeypatch.setattr(
        "omop_alchemy.maintenance.cli_backup.resolve_backend",
        lambda engine: FakeBackend(),
    )
    monkeypatch.setattr(
        "omop_alchemy.maintenance.cli_backup.require_backend_support",
        lambda *args, **kwargs: None,
    )

    result = _single_connection_backup(
        pg_engine,
        pg_resolved,
        output_path=tmp_path / "backup.dump",
        backup_format=BackupFormat.CUSTOM,
        dry_run=True,
    )

    assert MAINTENANCE_SCHEMA in captured["schemas"]
    assert MAINTENANCE_SCHEMA in result.schema_names
    assert SCHEMA_REGISTRY_SCHEMA in captured["schemas"]
    assert SCHEMA_REGISTRY_SCHEMA in result.schema_names


def _context(pg_split):
    return MaintenanceContext(
        resolved=pg_split.resolved,
        engine=pg_split.primary,
        vocab_engine=pg_split.vocab,
    )


def test_pg_split_truncate_targets_only_vocabulary_database(pg_split):
    context = _context(pg_split)

    results = _truncate_tables(
        context,
        scope=TableCategory.VOCABULARY,
        cascade=True,
    )

    assert any(result.table_name == "concept" for result in results)
    assert not sa.inspect(pg_split.primary).has_table("concept", schema="public")
    assert sa.inspect(pg_split.vocab).has_table("concept", schema="public")
    with pg_split.vocab.connect() as connection:
        assert connection.execute(sa.text("SELECT COUNT(*) FROM public.concept")).scalar_one() == 0


def test_pg_split_fulltext_install_populate_drop_use_vocab_database(pg_split):
    context = _context(pg_split)

    installed = _install_fulltext_columns(context)
    assert installed
    populated = populate_fulltext_columns(context)
    assert populated
    dropped = drop_fulltext_columns(context)
    assert dropped
    assert not sa.inspect(pg_split.primary).has_table("concept", schema="public")


def test_pg_split_fk_status_and_toggle_use_vocabulary_database(pg_split):
    context = _context(pg_split)

    disabled = manage_foreign_key_triggers(
        context, enable=False, vocabulary_only=True
    )
    status = collect_foreign_key_trigger_status(
        context, vocabulary_included=True
    )
    enabled = manage_foreign_key_triggers(
        context, enable=True, vocabulary_only=True
    )

    assert disabled and enabled
    assert any(result.table_name == "concept" for result in status)
    assert not sa.inspect(pg_split.primary).has_table("concept", schema="public")
