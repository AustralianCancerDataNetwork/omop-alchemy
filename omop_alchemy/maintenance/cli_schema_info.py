"""Environment inspection domain: package version, dependency status, connection state, and per-command readiness checks."""

from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata
import importlib.util
import shutil

import sqlalchemy as sa
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from oa_configurator import ResolvedCDMDatabase, Resolver, load_stack_config
from oa_configurator.loader import DEFAULT_CONFIG_PATH
from omop_alchemy.backends.resolve import SupportedDialect
from omop_alchemy.config import OmopAlchemyConfig

from ._cli_utils import Status
from .cli_schema_tables import collect_missing_tables
from .tables import (
    TableCategory,
    select_maintenance_tables,
)


def _backend_label(dialect_name: str) -> str:
    from ..backends.resolve import _DIALECT_TO_BACKEND_MAP, SupportedDialect
    try:
        return _DIALECT_TO_BACKEND_MAP[SupportedDialect(dialect_name)].name
    except (ValueError, KeyError):
        return dialect_name


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
    engine_created: bool
    engine_error: str | None
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


def _command_support_for_unavailable_engine(detail: str) -> tuple[CommandSupport, ...]:
    """Return a full CommandSupport tuple with every command marked blocked, used when the engine cannot be created."""
    blocked = Status.BLOCKED
    return (
        CommandSupport("doctor", "Any SQLAlchemy backend", blocked, detail),
        CommandSupport("data-summary", "Any SQLAlchemy backend", blocked, detail),
        CommandSupport("analyze-tables", "PostgreSQL/SQLite", blocked, detail),
        CommandSupport("create-missing-tables", "Any SQLAlchemy backend", blocked, detail),
        CommandSupport("indexes disable", "Any SQLAlchemy backend", blocked, detail),
        CommandSupport("indexes enable", "Any SQLAlchemy backend", blocked, detail),
        CommandSupport("reconcile-schema", "Any SQLAlchemy backend", blocked, detail),
        CommandSupport("load-vocab-source", "SQLite/PostgreSQL + Athena CSV source", blocked, detail),
        CommandSupport("backup-database", "PostgreSQL + pg_dump", blocked, detail),
        CommandSupport("restore-database", "PostgreSQL + pg_restore/psql", blocked, detail),
        CommandSupport("fulltext install", "PostgreSQL", blocked, detail),
        CommandSupport("fulltext populate", "PostgreSQL", blocked, detail),
        CommandSupport("fulltext drop", "PostgreSQL", blocked, detail),
        CommandSupport("reset-sequences", "PostgreSQL", blocked, detail),
        CommandSupport("truncate-tables", "PostgreSQL", blocked, detail),
        CommandSupport("foreign-keys disable", "PostgreSQL", blocked, detail),
        CommandSupport("foreign-keys enable", "PostgreSQL", blocked, detail),
        CommandSupport("foreign-keys enable --strict", "PostgreSQL", blocked, detail),
        CommandSupport("foreign-keys status", "PostgreSQL", blocked, detail),
        CommandSupport("foreign-keys validate", "PostgreSQL", blocked, detail),
    )


def _command_support_for_backend(
    *,
    backend: str,
    engine_created: bool,
    engine_error: str | None,
    connection_ready: bool,
    connection_error: str | None,
    pg_dump_path: str | None,
    pg_restore_path: str | None,
    psql_path: str | None,
) -> tuple[CommandSupport, ...]:
    """Compute the readiness status of every CLI command given the current backend, connection state, and tool availability."""
    current_backend = _backend_label(backend)
    if not engine_created:
        blocked_detail = (
            f"Backend resolved to {current_backend}, but the engine could not be created: {engine_error}"
            if engine_error
            else f"Backend resolved to {current_backend}, but the engine could not be created."
        )
    else:
        blocked_detail = (
            f"Backend resolved to {current_backend}, but the connection test failed: {connection_error}"
            if connection_error
            else f"Backend resolved to {current_backend}, but the connection test failed."
        )
    portable_status = Status.READY if connection_ready else Status.BLOCKED
    portable_detail = (
        f"Ready on {current_backend}." if connection_ready else blocked_detail
    )

    if backend == SupportedDialect.POSTGRESQL:
        analyze_status = portable_status
        analyze_detail = (
            "Ready on PostgreSQL; ANALYZE and VACUUM ANALYZE are both supported."
            if connection_ready
            else blocked_detail
        )
        enable_indexes_status = portable_status
        enable_indexes_detail = (
            "Ready on PostgreSQL; index DDL and clustering metadata are both supported."
            if connection_ready
            else blocked_detail
        )
        postgresql_status = portable_status
        postgresql_detail = "Ready on PostgreSQL." if connection_ready else blocked_detail
        vocab_load_status = portable_status
        vocab_load_detail = (
            "Ready on PostgreSQL when an Athena source path is configured."
            if connection_ready
            else blocked_detail
        )
    elif backend == "sqlite":
        analyze_status = Status.LIMITED if connection_ready else Status.BLOCKED
        analyze_detail = (
            "Ready on SQLite; ANALYZE is supported, but `--vacuum` is unavailable."
            if connection_ready
            else blocked_detail
        )
        enable_indexes_status = Status.LIMITED if connection_ready else Status.BLOCKED
        enable_indexes_detail = (
            "Ready on SQLite; index DDL is supported, but clustering metadata will be skipped."
            if connection_ready
            else blocked_detail
        )
        postgresql_status = Status.UNSUPPORTED if connection_ready else Status.BLOCKED
        postgresql_detail = (
            f"Requires PostgreSQL. Current backend: {current_backend}."
            if connection_ready
            else blocked_detail
        )
        vocab_load_status = portable_status
        vocab_load_detail = (
            "Ready on SQLite when an Athena source path is configured."
            if connection_ready
            else blocked_detail
        )
    else:
        analyze_status = Status.UNSUPPORTED if connection_ready else Status.BLOCKED
        analyze_detail = (
            f"Requires PostgreSQL or SQLite. Current backend: {current_backend}."
            if connection_ready
            else blocked_detail
        )
        enable_indexes_status = Status.LIMITED if connection_ready else Status.BLOCKED
        enable_indexes_detail = (
            f"Ready on {current_backend}; index DDL is supported, but clustering metadata will be skipped."
            if connection_ready
            else blocked_detail
        )
        postgresql_status = Status.UNSUPPORTED if connection_ready else Status.BLOCKED
        postgresql_detail = (
            f"Requires PostgreSQL. Current backend: {current_backend}."
            if connection_ready
            else blocked_detail
        )
        vocab_load_status = Status.UNSUPPORTED if connection_ready else Status.BLOCKED
        vocab_load_detail = (
            f"Requires SQLite or PostgreSQL plus a configured Athena source path. Current backend: {current_backend}."
            if connection_ready
            else blocked_detail
        )

    return (
        CommandSupport("doctor", "Any SQLAlchemy backend", portable_status, portable_detail),
        CommandSupport("data-summary", "Any SQLAlchemy backend", portable_status, portable_detail),
        CommandSupport("analyze-tables", "PostgreSQL/SQLite", analyze_status, analyze_detail),
        CommandSupport("create-missing-tables", "Any SQLAlchemy backend", portable_status, portable_detail),
        CommandSupport("indexes disable", "Any SQLAlchemy backend", portable_status, portable_detail),
        CommandSupport("indexes enable", "Any SQLAlchemy backend", enable_indexes_status, enable_indexes_detail),
        CommandSupport("reconcile-schema", "Any SQLAlchemy backend", portable_status, portable_detail),
        CommandSupport("load-vocab-source", "SQLite/PostgreSQL + Athena CSV source", vocab_load_status, vocab_load_detail),
        CommandSupport(
            "backup-database",
            "PostgreSQL + pg_dump",
            (
                Status.READY
                if connection_ready and backend == SupportedDialect.POSTGRESQL and pg_dump_path is not None
                else Status.BLOCKED
                if backend == SupportedDialect.POSTGRESQL
                else Status.UNSUPPORTED
                if connection_ready
                else Status.BLOCKED
            ),
            (
                "Ready on PostgreSQL; `pg_dump` is available."
                if connection_ready and backend == SupportedDialect.POSTGRESQL and pg_dump_path is not None
                else "PostgreSQL is configured, but `pg_dump` is not on PATH."
                if connection_ready and backend == SupportedDialect.POSTGRESQL
                else f"Requires PostgreSQL. Current backend: {current_backend}."
                if connection_ready
                else blocked_detail
            ),
        ),
        CommandSupport(
            "restore-database",
            "PostgreSQL + pg_restore/psql",
            (
                Status.READY
                if connection_ready and backend == SupportedDialect.POSTGRESQL and (pg_restore_path is not None or psql_path is not None)
                else Status.BLOCKED
                if backend == SupportedDialect.POSTGRESQL
                else Status.UNSUPPORTED
                if connection_ready
                else Status.BLOCKED
            ),
            (
                "Ready on PostgreSQL; restore client tooling is available."
                if connection_ready and backend == SupportedDialect.POSTGRESQL and (pg_restore_path is not None or psql_path is not None)
                else "PostgreSQL is configured, but neither `pg_restore` nor `psql` is on PATH."
                if connection_ready and backend == SupportedDialect.POSTGRESQL
                else f"Requires PostgreSQL. Current backend: {current_backend}."
                if connection_ready
                else blocked_detail
            ),
        ),
        CommandSupport("fulltext install", "PostgreSQL", postgresql_status, postgresql_detail),
        CommandSupport("fulltext populate", "PostgreSQL", postgresql_status, postgresql_detail),
        CommandSupport("fulltext drop", "PostgreSQL", postgresql_status, postgresql_detail),
        CommandSupport("reset-sequences", "PostgreSQL", postgresql_status, postgresql_detail),
        CommandSupport("truncate-tables", "PostgreSQL", postgresql_status, postgresql_detail),
        CommandSupport("foreign-keys disable", "PostgreSQL", postgresql_status, postgresql_detail),
        CommandSupport("foreign-keys enable", "PostgreSQL", postgresql_status, postgresql_detail),
        CommandSupport("foreign-keys enable --strict", "PostgreSQL", postgresql_status, postgresql_detail),
        CommandSupport("foreign-keys status", "PostgreSQL", postgresql_status, postgresql_detail),
        CommandSupport("foreign-keys validate", "PostgreSQL", postgresql_status, postgresql_detail),
    )


def collect_maintenance_info(
    *,
    vocabulary_included: bool = True,
) -> MaintenanceInfo:
    """Probe the current environment: resolve config, attempt a connection, and assess per-command readiness."""
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

    db_schema: str | None = None
    engine_url: str | None = None
    backend: str | None = None
    engine: sa.engine.Engine | None = None
    engine_created = False
    engine_error: str | None = None
    connection_ready = False
    connection_error: str | None = None
    existing_table_count: int | None = None
    missing_table_count: int | None = None

    db_name = OmopAlchemyConfig.model_fields["cdm_db"].default
    try:
        stack = load_stack_config()
        resolver = Resolver(stack)
        db_name = resolver.resolve_package_config(OmopAlchemyConfig).cdm_db
        resolved = resolver.resolve_database(db_name)
        if not isinstance(resolved, ResolvedCDMDatabase):
            raise ValueError(
                f"OmopAlchemyConfig.cdm_db must resolve to a CDM database, got "
                f"{type(resolved).__name__}"
            )
        db_schema = resolved.schema_name
        raw_url = sa.engine.make_url(resolved.connection.url)
        engine_url = raw_url.render_as_string(hide_password=True)
        backend = raw_url.get_backend_name()
    except (FileNotFoundError, ValueError, ArgumentError, KeyError) as exc:
        engine_error = f"Could not resolve engine configuration: {exc}"
    else:
        from omop_alchemy.config import create_cdm_engine
        try:
            engine = create_cdm_engine(resolved)
            engine_created = True
        except RuntimeError as exc:
            engine_error = str(exc)

    if engine is not None:
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
            connection_ready = True
            missing_tables = collect_missing_tables(
                engine,
                db_schema=db_schema,
                vocabulary_included=vocabulary_included,
            )
            missing_table_count = len(missing_tables)
            existing_table_count = len(managed_tables) - missing_table_count
        except SQLAlchemyError as exc:
            connection_error = f"{exc.__class__.__name__}: {exc}"
        except Exception as exc:
            connection_error = str(exc)
        finally:
            engine.dispose()

    if backend is None:
        command_support = _command_support_for_unavailable_engine(
            engine_error or "No engine configuration could be resolved."
        )
    else:
        command_support = _command_support_for_backend(
            backend=backend,
            engine_created=engine_created,
            engine_error=engine_error,
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
        resource_name=db_name,
        db_schema=db_schema,
        engine_url=engine_url,
        backend=backend,
        engine_created=engine_created,
        engine_error=engine_error,
        connection_ready=connection_ready,
        connection_error=connection_error,
        managed_table_count=len(managed_tables),
        existing_table_count=existing_table_count,
        missing_table_count=missing_table_count,
        vocabulary_included=vocabulary_included,
        dependencies=dependencies,
        command_support=command_support,
    )
