import sqlalchemy as sa
import pytest
from typer.testing import CliRunner

from omop_alchemy.maintenance.cli import app
from omop_alchemy.maintenance._cli_utils import Status
from omop_alchemy.maintenance.cli_schema import create_missing_tables
from omop_alchemy.maintenance.cli_foreign_keys import (
    ForeignKeyConstraintViolation,
    validate_foreign_key_constraints,
    _collect_fk_info,
    collect_foreign_key_trigger_status,
    manage_foreign_key_triggers,
)
from oa_configurator import CDMDatabaseConfig, ConnectionConfig, StackConfig

runner = CliRunner()


def _engine(tmp_path):
    return sa.create_engine(f"sqlite:///{tmp_path / 'foreign_keys.db'}", future=True)


def test_collect_fk_info_finds_participating_tables(tmp_path):
    """Test _collect_fk_info finds participating tables."""
    engine = _engine(tmp_path)
    create_missing_tables(engine)

    targets = {
        target.table_name: target
        for target in _collect_fk_info(engine)
    }

    assert "person" in targets
    assert targets["person"].incoming_constraint_count > 0


def test_manage_foreign_key_triggers_supports_dry_run(tmp_path):
    """Test manage foreign key triggers supports dry run."""
    engine = _engine(tmp_path)
    create_missing_tables(engine)

    with pytest.raises(RuntimeError) as exc_info:
        manage_foreign_key_triggers(
            engine,
            enable=False,
            dry_run=True,
        )

    assert "not supported by the SQLite backend" in str(exc_info.value)


def test_collect_foreign_key_trigger_status_is_safe_on_sqlite(tmp_path):
    """Test collect foreign key trigger status is safe on sqlite."""
    engine = _engine(tmp_path)
    create_missing_tables(engine)

    with pytest.raises(RuntimeError) as exc_info:
        collect_foreign_key_trigger_status(engine)

    assert "not supported by the SQLite backend" in str(exc_info.value)


def test_validate_foreign_key_constraints_is_safe_on_sqlite(tmp_path):
    """Test validate foreign key constraints is safe on sqlite."""
    engine = _engine(tmp_path)
    create_missing_tables(engine)

    with pytest.raises(RuntimeError) as exc_info:
        validate_foreign_key_constraints(engine)

    assert "not supported by the SQLite backend" in str(exc_info.value)


def test_disable_foreign_keys_cli_fails_gracefully_for_sqlite(monkeypatch):
    """Test disable foreign keys cli fails gracefully for sqlite."""

    cfg = StackConfig.for_session(
        connections={"db": ConnectionConfig(dialect="sqlite", database_name=":memory:")},
        databases={"cdm_db": CDMDatabaseConfig(connection="db", schema_name="main")},
    )
    monkeypatch.setattr(
        "omop_alchemy.config.load_stack_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        "omop_alchemy.config.load_stack_config",
        lambda: cfg,
    )

    result = runner.invoke(
        app,
        ["foreign-keys", "disable", "--dry-run"],
    )

    assert result.exit_code == 1
    assert "not supported by the SQLite" in result.stdout


def _make_fake_backend():
    from omop_alchemy.backends.base import Backend

    class _FakeBackend(Backend):
        @property
        def name(self) -> str:
            return "FakePostgres"

        @property
        def dialect(self) -> str:
            return "postgresql"

        def analyze_table(self, conn, table_name, db_schema, *, vacuum=False) -> None:
            pass

        def toggle_fk_triggers(self, conn, table_name, db_schema, *, enable: bool) -> None:
            action = "ENABLE" if enable else "DISABLE"
            conn.exec_driver_sql(f"ALTER TABLE {table_name} {action} TRIGGER ALL")

        def count_fk_violations(self, conn, *, source_table, referred_table, constraint_name, db_schema=None):  # type: ignore[override]
            return 0

    return _FakeBackend()


def test_manage_foreign_key_triggers_strict_does_not_enable_on_validation_failure(monkeypatch):
    """Test manage foreign key triggers strict does not enable on validation failure."""
    statements: list[str] = []

    class _FakeConnection:
        def exec_driver_sql(self, statement: str):
            statements.append(statement)
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _FakeEngine:
        def begin(self):
            return _FakeConnection()

    monkeypatch.setattr(
        "omop_alchemy.maintenance.cli_foreign_keys.resolve_backend",
        lambda engine: _make_fake_backend(),
    )
    monkeypatch.setattr(
        "omop_alchemy.maintenance.cli_foreign_keys._collect_fk_info",
        lambda engine, *, db_schema=None, vocabulary_included=False: [
            type("Target", (), {
                "table_name": "person",
                "category": "clinical",
                "model_name": "Person",
                "model_module": "omop_alchemy.cdm.model.clinical.person",
                "outgoing_constraint_count": 1,
                "incoming_constraint_count": 2,
            })(),
            type("Target", (), {
                "table_name": "visit_occurrence",
                "category": "health_system",
                "model_name": "VisitOccurrence",
                "model_module": "omop_alchemy.cdm.model.health_system.visit_occurrence",
                "outgoing_constraint_count": 2,
                "incoming_constraint_count": 0,
            })(),
        ],
    )
    monkeypatch.setattr(
        "omop_alchemy.maintenance.cli_foreign_keys._collect_strict_validation_failures",
        lambda connection, backend, *, db_schema=None, vocabulary_included=False: {
            "visit_occurrence": [
                ForeignKeyConstraintViolation(
                    source_table_name="visit_occurrence",
                    referred_table_name="person",
                    constraint_name="fk_visit_occurrence_person_id_person",
                    violation_count=3,
                )
            ]
        },
    )

    results = manage_foreign_key_triggers(
        _FakeEngine(),  # type: ignore[arg-type]
        enable=True,
        strict=True,
    )

    assert statements == []
    assert [result.status for result in results] == ["skipped", "failed"]
    assert "no FK triggers were enabled" in results[0].detail
    assert "fk_visit_occurrence_person_id_person" in results[1].detail


def test_manage_foreign_key_triggers_strict_enables_when_validation_passes(monkeypatch):
    """Test manage foreign key triggers strict enables when validation passes."""
    statements: list[str] = []

    class _FakeConnection:
        def exec_driver_sql(self, statement: str):
            statements.append(statement)
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _FakeEngine:
        def begin(self):
            return _FakeConnection()

    monkeypatch.setattr(
        "omop_alchemy.maintenance.cli_foreign_keys.resolve_backend",
        lambda engine: _make_fake_backend(),
    )
    monkeypatch.setattr(
        "omop_alchemy.maintenance.cli_foreign_keys._collect_fk_info",
        lambda engine, *, db_schema=None, vocabulary_included=False: [
            type("Target", (), {
                "table_name": "person",
                "category": "clinical",
                "model_name": "Person",
                "model_module": "omop_alchemy.cdm.model.clinical.person",
                "outgoing_constraint_count": 1,
                "incoming_constraint_count": 2,
            })(),
        ],
    )
    monkeypatch.setattr(
        "omop_alchemy.maintenance.cli_foreign_keys._collect_strict_validation_failures",
        lambda connection, backend, *, db_schema=None, vocabulary_included=False: {},
    )

    results = manage_foreign_key_triggers(
        _FakeEngine(),  # type: ignore[arg-type]
        enable=True,
        strict=True,
    )

    assert statements == ["ALTER TABLE person ENABLE TRIGGER ALL"]
    assert results[0].status == "applied"
    assert "Strict FK validation passed" in results[0].detail


def test_enable_foreign_keys_strict_cli_invokes_strict_management(monkeypatch):
    """Test enable foreign keys strict cli invokes strict management."""

    calls: dict[str, object] = {}

    cfg = StackConfig.for_session(
        connections={"db": ConnectionConfig(dialect="sqlite", database_name=":memory:")},
        databases={"cdm_db": CDMDatabaseConfig(connection="db", schema_name="main")},
    )
    monkeypatch.setattr(
        "omop_alchemy.config.load_stack_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        "omop_alchemy.config.load_stack_config",
        lambda: cfg,
    )

    def fake_manage_foreign_key_triggers(
        engine: object,
        *,
        enable: bool,
        db_schema: str | None = None,
        vocabulary_included: bool = False,
        dry_run: bool = False,
        strict: bool = False,
    ):
        calls["engine"] = engine
        calls["enable"] = enable
        calls["db_schema"] = db_schema
        calls["vocabulary_included"] = vocabulary_included
        calls["dry_run"] = dry_run
        calls["strict"] = strict
        return []

    monkeypatch.setattr(
        "omop_alchemy.maintenance.cli_foreign_keys.manage_foreign_key_triggers",
        fake_manage_foreign_key_triggers,
    )

    result = runner.invoke(
        app,
        ["foreign-keys", "enable", "--strict", "--dry-run"],
    )

    assert result.exit_code == 0
    assert calls["strict"] is True
    assert calls["enable"] is True
    assert "foreign-keys enable" in result.stdout


def test_validate_foreign_key_constraints_reports_failures(monkeypatch):
    """Test validate foreign key constraints reports failures."""
    class _FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _FakeEngine:
        def connect(self):
            return _FakeConnection()

    monkeypatch.setattr(
        "omop_alchemy.maintenance.cli_foreign_keys.resolve_backend",
        lambda engine: _make_fake_backend(),
    )
    monkeypatch.setattr(
        "omop_alchemy.maintenance.cli_foreign_keys._collect_fk_info",
        lambda engine, *, db_schema=None, vocabulary_included=False: [
            type("Target", (), {
                "table_name": "person",
                "category": "clinical",
                "model_name": "Person",
                "model_module": "omop_alchemy.cdm.model.clinical.person",
                "outgoing_constraint_count": 1,
                "incoming_constraint_count": 2,
            })(),
            type("Target", (), {
                "table_name": "visit_occurrence",
                "category": "health_system",
                "model_name": "VisitOccurrence",
                "model_module": "omop_alchemy.cdm.model.health_system.visit_occurrence",
                "outgoing_constraint_count": 2,
                "incoming_constraint_count": 0,
            })(),
        ],
    )
    monkeypatch.setattr(
        "omop_alchemy.maintenance.cli_foreign_keys._collect_strict_validation_failures",
        lambda connection, backend, *, db_schema=None, vocabulary_included=False: {
            "visit_occurrence": [
                ForeignKeyConstraintViolation(
                    source_table_name="visit_occurrence",
                    referred_table_name="person",
                    constraint_name="fk_visit_occurrence_person_id_person",
                    violation_count=3,
                )
            ]
        },
    )

    report = validate_foreign_key_constraints(_FakeEngine())  # type: ignore[arg-type]

    assert [result.status for result in report.results] == ["passed", "failed"]
    assert report.results[0].violating_row_count == 0
    assert report.results[1].violating_constraint_count == 1
    assert "fk_visit_occurrence_person_id_person" in report.results[1].detail
    assert len(report.violations) == 1


def test_foreign_keys_validate_cli_invokes_validation(monkeypatch):
    """Test foreign keys validate cli invokes validation."""

    calls: dict[str, object] = {}

    cfg = StackConfig.for_session(
        connections={"db": ConnectionConfig(dialect="sqlite", database_name=":memory:")},
        databases={"cdm_db": CDMDatabaseConfig(connection="db", schema_name="main")},
    )
    monkeypatch.setattr(
        "omop_alchemy.config.load_stack_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        "omop_alchemy.config.load_stack_config",
        lambda: cfg,
    )

    def fake_validate_foreign_key_constraints(
        engine: object,
        *,
        db_schema: str | None = None,
        vocabulary_included: bool = False,
    ):
        from omop_alchemy.maintenance.cli_foreign_keys import (
            ForeignKeyConstraintViolation,
            ForeignKeyValidationReport,
            ForeignKeyValidationResult,
        )
        from omop_alchemy.maintenance.tables import TableCategory

        calls["engine"] = engine
        calls["db_schema"] = db_schema
        calls["vocabulary_included"] = vocabulary_included
        return ForeignKeyValidationReport(
            results=(
                ForeignKeyValidationResult(
                    table_name="visit_occurrence",
                    category=TableCategory.HEALTH_SYSTEM,
                    outgoing_constraint_count=2,
                    incoming_constraint_count=0,
                    violating_constraint_count=1,
                    violating_row_count=3,
                    status=Status.FAILED,
                    detail="3 violating row(s) across 1 constraint(s): fk_visit_occurrence_person_id_person (3)",
                ),
            ),
            violations=(
                ForeignKeyConstraintViolation(
                    source_table_name="visit_occurrence",
                    referred_table_name="person",
                    constraint_name="fk_visit_occurrence_person_id_person",
                    violation_count=3,
                ),
            ),
        )

    monkeypatch.setattr(
        "omop_alchemy.maintenance.cli_foreign_keys.validate_foreign_key_constraints",
        fake_validate_foreign_key_constraints,
    )

    result = runner.invoke(
        app,
        ["foreign-keys", "validate"],
    )

    assert result.exit_code == 0
    assert "foreign-keys validate" in result.stdout
    assert "Violations" in result.stdout
    assert "visit_occurrence" in result.stdout
    assert "Violating rows" in result.stdout
