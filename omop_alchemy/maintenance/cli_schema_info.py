"""Environment inspection domain: package version, dependency status, connection state, and per-command readiness checks."""

from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata
import importlib.util
import shutil

from sqlalchemy.exc import SQLAlchemyError

from oa_configurator.loader import DEFAULT_CONFIG_PATH

from ..backends import Backend, BackendNotSupportedError, backend_support_note, backend_supports, resolve_backend
from ._cli_utils import Status
from .context import MaintenanceContext
from .cli_schema_tables import collect_missing_tables
from .tables import (
    TableCategory,
    select_maintenance_tables,
)


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DependencyStatus:
    """Installation status of a Python package or external tool dependency."""

    name: str
    installed: bool
    version: str | None


@dataclass(frozen=True)
class CommandSupport:
    """Readiness assessment for one CLI command given the current backend and connection state."""

    command_name: str
    requirement: str
    status: Status
    detail: str


@dataclass(frozen=True)
class MaintenanceInfo:
    """Full environment snapshot: package version, connection state, and per-command readiness."""

    package_version: str
    cli_path: str | None
    pg_dump_path: str | None
    pg_restore_path: str | None
    psql_path: str | None
    config_file: str
    config_exists: bool
    resource_name: str
    db_schema: str | None
    engine_url: str | None
    backend: str | None
    connection_ready: bool
    connection_error: str | None
    managed_table_count: int
    existing_table_count: int | None
    missing_table_count: int | None
    vocabulary_included: bool
    dependencies: tuple[DependencyStatus, ...]
    command_support: tuple[CommandSupport, ...]


def _package_version() -> str:
    """Return the installed omop-alchemy package version string."""
    return importlib.metadata.version("omop-alchemy")


def _dependency_status(distribution_name: str, module_name: str) -> DependencyStatus:
    """Check whether a Python package is importable and return its installed version if found."""
    installed = importlib.util.find_spec(module_name) is not None
    version: str | None = None
    if installed:
        try:
            version = importlib.metadata.version(distribution_name)
        except importlib.metadata.PackageNotFoundError:
            version = None
    return DependencyStatus(name=distribution_name, installed=installed, version=version)


def _external_dependency_status(name: str, executable_name: str) -> DependencyStatus:
    """Check whether an external CLI tool is on PATH and return a DependencyStatus (version always None)."""
    return DependencyStatus(
        name=name,
        installed=shutil.which(executable_name) is not None,
        version=None,
    )


def _command_support_for_backend(
    *,
    backend: Backend | None,
    backend_label: str,
    connection_ready: bool,
    connection_error: str | None,
    pg_dump_path: str | None,
    pg_restore_path: str | None,
    psql_path: str | None,
) -> tuple[CommandSupport, ...]:
    """Compute the readiness status of every CLI command given the current backend, 
    connection state, and tool availability.
    """
    blocked_detail = (
        f"Backend resolved to {backend_label}, but the connection test failed: {connection_error}"
        if connection_error
        else f"Backend resolved to {backend_label}, but the connection test failed."
    )
    portable_status = Status.READY if connection_ready else Status.BLOCKED
    portable_detail = f"Ready on {backend_label}." if connection_ready else blocked_detail

    def supports(method_name: str) -> bool:
        return backend is not None and backend_supports(backend, method_name)

    def gated(method_name: str) -> tuple[Status, str]:
        """READY/UNSUPPORTED/BLOCKED status and detail for a command that needs method_name."""
        if not connection_ready:
            return Status.BLOCKED, blocked_detail
        if supports(method_name):
            return Status.READY, f"Ready on {backend_label}."
        return Status.UNSUPPORTED, f"{backend_support_note(method_name)} Current backend: {backend_label}."

    analyze_status, analyze_detail = gated("analyze_table")
    enable_indexes_status, enable_indexes_detail = (
        (Status.BLOCKED, blocked_detail)
        if not connection_ready
        else (Status.READY, f"Ready on {backend_label}; index DDL and clustering metadata are both supported.")
        if supports("cluster_table")
        else (Status.LIMITED, f"Ready on {backend_label}; index DDL is supported, but clustering metadata will be skipped.")
    )
    backup_status, backup_detail = (
        (Status.BLOCKED, blocked_detail)
        if not connection_ready
        else (Status.UNSUPPORTED, f"{backend_support_note('prepare_backup')} Current backend: {backend_label}.")
        if not supports("prepare_backup")
        else (Status.READY, f"Ready on {backend_label}; `pg_dump` is available.")
        if pg_dump_path is not None
        else (Status.BLOCKED, f"{backend_label} is configured, but `pg_dump` is not on PATH.")
    )
    restore_status, restore_detail = (
        (Status.BLOCKED, blocked_detail)
        if not connection_ready
        else (Status.UNSUPPORTED, f"{backend_support_note('prepare_restore')} Current backend: {backend_label}.")
        if not supports("prepare_restore")
        else (Status.READY, f"Ready on {backend_label}; restore client tooling is available.")
        if pg_restore_path is not None or psql_path is not None
        else (Status.BLOCKED, f"{backend_label} is configured, but neither `pg_restore` nor `psql` is on PATH.")
    )
    fulltext_status, fulltext_detail = gated("install_fulltext_on_table")
    reset_sequences_status, reset_sequences_detail = gated("set_sequence_value")
    truncate_status, truncate_detail = gated("truncate_table_batch")
    fk_trigger_status, fk_trigger_detail = gated("toggle_fk_triggers")
    fk_status_status, fk_status_detail = gated("get_fk_trigger_counts")
    fk_validate_status, fk_validate_detail = gated("count_fk_violations")

    return (
        CommandSupport("doctor", "Any SQLAlchemy backend", portable_status, portable_detail),
        CommandSupport("data-summary", "Any SQLAlchemy backend", portable_status, portable_detail),
        CommandSupport("analyze-tables", backend_support_note("analyze_table"), analyze_status, analyze_detail),
        CommandSupport("create-missing-tables", "Any SQLAlchemy backend", portable_status, portable_detail),
        CommandSupport("indexes disable", "Any SQLAlchemy backend", portable_status, portable_detail),
        CommandSupport("indexes enable", "Any SQLAlchemy backend", enable_indexes_status, enable_indexes_detail),
        CommandSupport("reconcile-schema", "Any SQLAlchemy backend", portable_status, portable_detail),
        CommandSupport(
            "load-vocab-source",
            "Any SQLAlchemy backend + Athena CSV source",
            portable_status,
            f"Ready on {backend_label} when an Athena source path is configured." if connection_ready else blocked_detail,
        ),
        CommandSupport("backup-database", f"{backend_support_note('prepare_backup')} pg_dump", backup_status, backup_detail),
        CommandSupport(
            "restore-database", f"{backend_support_note('prepare_restore')} pg_restore/psql", restore_status, restore_detail
        ),
        CommandSupport("fulltext install", backend_support_note("install_fulltext_on_table"), fulltext_status, fulltext_detail),
        CommandSupport("fulltext populate", backend_support_note("populate_fulltext_on_table"), fulltext_status, fulltext_detail),
        CommandSupport("fulltext drop", backend_support_note("drop_fulltext_on_table"), fulltext_status, fulltext_detail),
        CommandSupport(
            "reset-sequences", backend_support_note("set_sequence_value"), reset_sequences_status, reset_sequences_detail
        ),
        CommandSupport("truncate-tables", backend_support_note("truncate_table_batch"), truncate_status, truncate_detail),
        CommandSupport("foreign-keys disable", backend_support_note("toggle_fk_triggers"), fk_trigger_status, fk_trigger_detail),
        CommandSupport("foreign-keys enable", backend_support_note("toggle_fk_triggers"), fk_trigger_status, fk_trigger_detail),
        CommandSupport(
            "foreign-keys enable --strict", backend_support_note("toggle_fk_triggers"), fk_trigger_status, fk_trigger_detail
        ),
        CommandSupport("foreign-keys status", backend_support_note("get_fk_trigger_counts"), fk_status_status, fk_status_detail),
        CommandSupport(
            "foreign-keys validate", backend_support_note("count_fk_violations"), fk_validate_status, fk_validate_detail
        ),
    )


def collect_maintenance_info(
    context: MaintenanceContext,
    *,
    vocabulary_included: bool = True,
) -> MaintenanceInfo:
    """Probe every engine's connection readiness and assess per-command support."""
    engine = context.engine
    pg_dump_path = shutil.which("pg_dump")
    pg_restore_path = shutil.which("pg_restore")
    psql_path = shutil.which("psql")
    config_file = DEFAULT_CONFIG_PATH
    dependencies = (
        _dependency_status("sqlalchemy", "sqlalchemy"),
        _dependency_status("typer", "typer"),
        _dependency_status("rich", "rich"),
        _dependency_status("psycopg", "psycopg"),
        _dependency_status("psycopg2-binary", "psycopg2"),
        _external_dependency_status("pg_dump", "pg_dump"),
        _external_dependency_status("pg_restore", "pg_restore"),
        _external_dependency_status("psql", "psql"),
    )
    managed_tables = select_maintenance_tables(
        exclude_categories=(() if vocabulary_included else (TableCategory.VOCABULARY,))
    )
    cli_path = shutil.which("omop-alchemy")

    engine_url = engine.url.render_as_string(hide_password=True)
    backend = engine.url.get_backend_name()
    try:
        backend_instance: Backend | None = resolve_backend(engine)
        resolved_backend_label = backend_instance.name
    except BackendNotSupportedError:
        backend_instance = None
        resolved_backend_label = backend
    connection_ready = False
    connection_error: str | None = None
    existing_table_count: int | None = None
    missing_table_count: int | None = None

    try:
        for candidate in context.engines:
            with candidate.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
        connection_ready = True
        missing_tables = collect_missing_tables(context, vocabulary_included=vocabulary_included)
        missing_table_count = len(missing_tables)
        existing_table_count = len(managed_tables) - missing_table_count
    except SQLAlchemyError as exc:
        connection_error = f"{exc.__class__.__name__}: {exc}"
    except Exception as exc:
        connection_error = str(exc)

    command_support = _command_support_for_backend(
        backend=backend_instance,
        backend_label=resolved_backend_label,
        connection_ready=connection_ready,
        connection_error=connection_error,
        pg_dump_path=pg_dump_path,
        pg_restore_path=pg_restore_path,
        psql_path=psql_path,
    )

    return MaintenanceInfo(
        package_version=_package_version(),
        cli_path=cli_path,
        pg_dump_path=pg_dump_path,
        pg_restore_path=pg_restore_path,
        psql_path=psql_path,
        config_file=str(config_file),
        config_exists=config_file.exists(),
        resource_name=context.resource_name,
        db_schema=context.resolved.schema_name,
        engine_url=engine_url,
        backend=backend,
        connection_ready=connection_ready,
        connection_error=connection_error,
        managed_table_count=len(managed_tables),
        existing_table_count=existing_table_count,
        missing_table_count=missing_table_count,
        vocabulary_included=vocabulary_included,
        dependencies=dependencies,
        command_support=command_support,
    )
