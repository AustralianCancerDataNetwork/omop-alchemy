import importlib
import pytest
from typer.testing import CliRunner
from oa_configurator import CDMDatabaseConfig, ConnectionConfig, StackConfig

from omop_alchemy.maintenance.cli import app
from omop_alchemy.maintenance._cli_utils import Status
from omop_alchemy.maintenance.cli_schema import create_missing_tables
from omop_alchemy.maintenance.tables import TableCategory
from omop_alchemy.maintenance.cli_tables import TruncateTableResult, truncate_tables

runner = CliRunner()
truncate_tables_module = importlib.import_module("omop_alchemy.maintenance.cli_tables")


def test_truncate_tables_requires_postgresql(fresh_engine):
    """Test truncate tables requires postgresql."""
    engine = fresh_engine

    with pytest.raises(RuntimeError) as exc_info:
        truncate_tables(engine, scope=TableCategory.CLINICAL, dry_run=True)

    assert "not supported by the SQLite backend" in str(exc_info.value)


def test_truncate_tables_reports_blocking_foreign_key_references(monkeypatch, fresh_engine):
    """Test truncate tables reports blocking foreign key references."""
    engine = fresh_engine
    create_missing_tables(engine, vocabulary_included=True)

    monkeypatch.setattr(truncate_tables_module, "require_backend_support", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError) as exc_info:
        truncate_tables(engine, scope=TableCategory.CLINICAL, dry_run=False)

    message = str(exc_info.value)
    assert "foreign key references from tables outside the current selection" in message
    assert "Blocking references:" in message
    assert "--cascade" in message


def test_truncate_tables_cli_requires_confirmation(monkeypatch):
    """Test truncate tables cli requires confirmation."""

    cfg = StackConfig.for_session(
        connections={"db": ConnectionConfig(dialect="sqlite", database_name=":memory:")},
        databases={"cdm_db": CDMDatabaseConfig(connection="db", schema_name="main")},
    )
    monkeypatch.setattr(
        "omop_alchemy.config.load_stack_config",
        lambda: cfg,
    )
    result = runner.invoke(app, ["truncate-tables", "--scope", "clinical"])

    assert result.exit_code == 1
    assert "--yes" in result.stdout


def test_truncate_tables_cli_invokes_management(monkeypatch):
    """Test truncate tables cli invokes management."""

    calls: dict[str, object] = {}

    cfg = StackConfig.for_session(
        connections={"db": ConnectionConfig(dialect="sqlite", database_name=":memory:")},
        databases={"cdm_db": CDMDatabaseConfig(connection="db", schema_name="main")},
    )
    monkeypatch.setattr(
        "omop_alchemy.config.load_stack_config",
        lambda: cfg,
    )

    def fake_truncate_tables(
        engine: object,
        *,
        db_schema: str | None = None,
        scope: TableCategory | None = None,
        table_names: tuple[str, ...] | None = None,
        restart_identities: bool = False,
        cascade: bool = False,
        dry_run: bool = False,
    ) -> list[TruncateTableResult]:
        calls["engine"] = engine
        calls["db_schema"] = db_schema
        calls["scope"] = scope
        calls["table_names"] = table_names
        calls["restart_identities"] = restart_identities
        calls["cascade"] = cascade
        calls["dry_run"] = dry_run
        return [
            TruncateTableResult(
                table_name="person",
                category=TableCategory.CLINICAL,
                row_count=10,
                status=Status.PLANNED,
                detail="table would be truncated",
            )
        ]

    monkeypatch.setattr(
        "omop_alchemy.maintenance.cli_tables.truncate_tables",
        fake_truncate_tables,
    )

    result = runner.invoke(
        app,
        [
            "truncate-tables",
            "--scope",
            "clinical",
            "--restart-identities",
            "--cascade",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert calls["scope"] == TableCategory.CLINICAL
    assert calls["table_names"] is None
    assert calls["restart_identities"] is True
    assert calls["cascade"] is True
    assert calls["dry_run"] is True
    assert "truncate-tables" in result.stdout
