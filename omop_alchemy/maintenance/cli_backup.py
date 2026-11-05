"""Backup and restore commands: pg_dump/pg_restore/psql on PostgreSQL, in-process on DuckDB."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
import subprocess

import sqlalchemy as sa
import typer
from oa_configurator import (
    SCHEMA_REGISTRY_SCHEMA,
    Dialect,
    ResolvedCDMDatabase,
    SchemaDriftError,
    ensure_schema,
)

from ..backends import (
    Backend,
    resolve_backend,
    require_backend_support,
    backend_support_note,
    backend_supports,
)
from ._cli_utils import (
    Status,
    dry_label,
    dry_status,
    omop_command
)
from .cli_tables import reset_model_sequences
from .context import MaintenanceContext
from ..config import MAINTENANCE_SCHEMA
from .ui import (
    console,
    render_backup_result,
    render_backup_summary,
    render_restore_result,
    render_restore_summary,
)


class BackupFormat(StrEnum):
    """Supported backup artifact formats.

    CUSTOM and PLAIN are pg_dump/psql output formats, PostgreSQL only.
    PARQUET is currently DuckDB-only: one Parquet file per table, produced
    in-process rather than by an external tool.
    """

    CUSTOM = "custom"
    PLAIN = "plain"
    PARQUET = "parquet"


FORMAT_SUFFIXES = {
    BackupFormat.CUSTOM: ".dump",
    BackupFormat.PLAIN: ".sql",
    BackupFormat.PARQUET: "",  # artifact is a directory of per-table files, not one file, so no file suffix applies
}


@dataclass(frozen=True)
class BackupResult:
    """Metadata and outcome for a single backup or restore operation, covering one physical connection."""

    file_path: str
    backup_format: BackupFormat
    status: Status
    detail: str
    database_name: str
    backend: str
    schema_names: tuple[str, ...]
    command: tuple[str, ...]
    tool_path: str


def _default_output_path(backup_format: BackupFormat) -> Path:
    """Return a timestamped default output path in the current directory matching the chosen backup format."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path.cwd() / f"omop-alchemy-backup-{timestamp}{FORMAT_SUFFIXES[backup_format]}"


def _vocab_sibling_path(primary_path: str, backup_format: BackupFormat) -> Path:
    """Sibling artifact path for the vocabulary connection's own backup, alongside primary_path."""
    primary = Path(primary_path)
    suffix = FORMAT_SUFFIXES[backup_format]
    stem = primary.name[: -len(suffix)] if primary.name.endswith(suffix) else primary.stem
    return primary.with_name(f"{stem}-vocab{suffix}")


def _in_process_backup(
    engine: sa.Engine,
    backend: Backend,
    schemas: list[str],
    *,
    output_path: str | Path | None,
    dry_run: bool,
) -> BackupResult:
    """Back up every schema this database entry claims, writing directly
    over engine's own connection instead of running an external tool."""
    resolved_output_path = Path(output_path) if output_path is not None else _default_output_path(BackupFormat.PARQUET)
    resolved_output_path = resolved_output_path.expanduser().resolve()
    if not dry_run:
        resolved_output_path.mkdir(parents=True, exist_ok=True)
        backend.backup_tables(engine, str(resolved_output_path), schemas=schemas)
    return BackupResult(
        file_path=str(resolved_output_path),
        backup_format=BackupFormat.PARQUET,
        status=dry_status(dry_run, applied=Status.CREATED),
        detail=dry_label(
            dry_run,
            f"Database backup would be created in-process by the {backend.name} backend.",
            f"Database backup created in-process by the {backend.name} backend.",
        ),
        database_name=str(engine.url.database),
        backend=engine.dialect.name,
        schema_names=tuple(schemas),
        command=(),
        tool_path="in-process",
    )


def _single_connection_backup(
    engine: sa.Engine,
    resolved: ResolvedCDMDatabase,
    *,
    output_path: str | Path | None,
    backup_format: BackupFormat,
    dry_run: bool,
) -> BackupResult:
    """Back up every schema this database entry claims on engine's own
    physical server, in one pg_dump invocation, or in-process for a
    backend with no external dump tool."""
    backend = resolve_backend(engine)
    with engine.connect() as connection:
        schemas = sorted(resolved.occupied_schemas(connection))
    if engine.dialect.name == Dialect.POSTGRESQL:
        schemas = sorted({*schemas, MAINTENANCE_SCHEMA, SCHEMA_REGISTRY_SCHEMA})

    if backend_supports(backend, "backup_tables"):
        return _in_process_backup(engine, backend, schemas, output_path=output_path, dry_run=dry_run)

    require_backend_support(backend, "prepare_backup", "Database backup")
    resolved_output_path = Path(output_path) if output_path is not None else _default_output_path(backup_format)
    resolved_output_path = resolved_output_path.expanduser().resolve()

    tool_path, command, env, database_name = backend.prepare_backup(
        engine,
        str(resolved_output_path),
        backup_format.value,
        schemas=schemas,
    )

    if not dry_run:
        resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(command, env=env, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            raise RuntimeError(
                "Database backup failed via `pg_dump`." + (f" {stderr}" if stderr else "")
            ) from exc
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"`pg_dump` executable not found at {tool_path!r}. "
                "It may have been removed from PATH after being resolved."
            ) from exc

    return BackupResult(
        file_path=str(resolved_output_path),
        backup_format=backup_format,
        status=dry_status(dry_run, applied=Status.CREATED),
        detail=dry_label(dry_run, "Database backup would be created with pg_dump.", "Database backup created with pg_dump."),
        database_name=database_name,
        backend=engine.dialect.name,
        schema_names=tuple(schemas),
        command=tuple(command),
        tool_path=tool_path,
    )


def _ensure_custom_restore_schemas(engine: sa.Engine, schemas: list[str]) -> None:
    """Create target schemas before a custom-format PostgreSQL restore."""
    with engine.begin() as connection:
        for schema in schemas:
            ensure_schema(connection, schema)


def _require_empty_plain_restore_target(engine: sa.Engine, schemas: list[str]) -> None:
    """Reject plain PostgreSQL restores when a dumped schema already exists."""
    collisions = sorted(set(sa.inspect(engine).get_schema_names()).intersection(schemas))
    if collisions:
        raise RuntimeError(
            "Plain-format restore needs an empty target; these dumped schemas "
            f"already exist: {', '.join(collisions)}. Drop those schemas or use custom format."
        )


def _execute_restore(
    engine: sa.Engine,
    schemas: list[str],
    backup_format: BackupFormat,
    prepared_restore: tuple[str, list[str], dict[str, str], str],
) -> None:
    """Run a restore after applying format-specific target preparation."""
    tool_path, command, env, _database_name = prepared_restore
    if backup_format is BackupFormat.PLAIN and engine.dialect.name == Dialect.POSTGRESQL:
        _require_empty_plain_restore_target(engine, schemas)
    elif backup_format is BackupFormat.CUSTOM:
        _ensure_custom_restore_schemas(engine, schemas)
    try:
        subprocess.run(command, env=env, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(
            "Database restore failed." + (f" {stderr}" if stderr else "")
        ) from exc
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Restore executable not found at {tool_path!r}. "
            "It may have been removed from PATH after being resolved."
        ) from exc


def _in_process_restore(
    engine: sa.Engine,
    backend: Backend,
    schemas: list[str],
    *,
    input_path: str | Path,
    dry_run: bool,
) -> BackupResult:
    """Restore every schema this database entry claims, reading directly
    over engine's own connection instead of running an external tool."""
    resolved_input_path = Path(input_path).expanduser().resolve()
    if not resolved_input_path.is_dir():
        raise RuntimeError(f"Backup artifact not found or not a directory: {resolved_input_path}")
    if not dry_run:
        backend.restore_tables(engine, str(resolved_input_path), schemas=schemas)
    return BackupResult(
        file_path=str(resolved_input_path),
        backup_format=BackupFormat.PARQUET,
        status=dry_status(dry_run),
        detail=dry_label(
            dry_run,
            f"Database restore would be executed in-process by the {backend.name} backend.",
            f"Database restore completed in-process by the {backend.name} backend.",
        ),
        database_name=str(engine.url.database),
        backend=engine.dialect.name,
        schema_names=tuple(schemas),
        command=(),
        tool_path="in-process",
    )


def _single_connection_restore(
    engine: sa.Engine,
    resolved: ResolvedCDMDatabase,
    *,
    input_path: str | Path,
    backup_format: BackupFormat,
    dry_run: bool,
) -> BackupResult:
    """Restore every schema this database entry claims on engine's own
    physical server, from one backup artifact, or in-process for a
    backend with no external restore tool."""
    backend = resolve_backend(engine)
    with engine.connect() as connection:
        schemas = sorted(resolved.occupied_schemas(connection))
    if engine.dialect.name == Dialect.POSTGRESQL:
        schemas = sorted({*schemas, MAINTENANCE_SCHEMA, SCHEMA_REGISTRY_SCHEMA})

    if backend_supports(backend, "restore_tables"):
        return _in_process_restore(engine, backend, schemas, input_path=input_path, dry_run=dry_run)

    require_backend_support(backend, "prepare_restore", "Database restore")
    resolved_input_path = Path(input_path).expanduser().resolve()
    if not resolved_input_path.exists():
        raise RuntimeError(f"Backup artifact not found: {resolved_input_path}")

    tool_path, command, env, database_name = backend.prepare_restore(
        engine,
        str(resolved_input_path),
        backup_format.value,
        schemas=schemas,
    )

    if not dry_run:
        _execute_restore(engine, schemas, backup_format, (tool_path, command, env, database_name))

    return BackupResult(
        file_path=str(resolved_input_path),
        backup_format=backup_format,
        status=dry_status(dry_run),
        detail=dry_label(dry_run, "Database restore would be executed using PostgreSQL client tools.", "Database restore completed using PostgreSQL client tools."),
        database_name=database_name,
        backend=engine.dialect.name,
        schema_names=tuple(schemas),
        command=tuple(command),
        tool_path=tool_path,
    )


def create_database_backup(
    context: MaintenanceContext,
    *,
    output_path: str | Path | None = None,
    backup_format: BackupFormat = BackupFormat.CUSTOM,
    include_vocab: bool = False,
    dry_run: bool = False,
) -> list[BackupResult]:
    """Back up every schema this database entry claims. Runs pg_dump unless dry_run is True.

    Parameters
    ----------
    context : MaintenanceContext
        Its vocabulary engine is backed up in a second artifact when
        include_vocab is True and the vocabulary has its own database; a
        shared one is already covered by the first artifact. The caller keeps
        ownership of both engines.
    include_vocab : bool, optional
        Also back up the vocabulary connection, in a second artifact
        alongside the first.
    """
    results = [
        _single_connection_backup(
            context.engine, 
            context.resolved, 
            output_path=output_path, 
            backup_format=backup_format, 
            dry_run=dry_run
        )
    ]

    if include_vocab and context.vocab_engine is not context.engine:
        vocab_output_path = _vocab_sibling_path(results[0].file_path, backup_format)
        results.append(
            _single_connection_backup(
                context.vocab_engine, 
                context.resolved, 
                output_path=vocab_output_path, 
                backup_format=backup_format, 
                dry_run=dry_run
            )
        )

    return results


def _register_restored_schemas(context: MaintenanceContext, input_path: str | Path) -> None:
    """Register restored claims or explain how to baseline an older backup."""
    from ..config import create_cdm_engines

    try:
        primary, vocab = create_cdm_engines(context.resolved, register_claims=True)
    except SchemaDriftError as exc:
        error_text = str(exc).lower()
        missing_baseline = any(
            marker in error_text
            for marker in (
                "no schema-registry baseline",
                "no schema-registry record exists",
            )
        )
        if not missing_baseline:
            raise
        database_name = context.resource_name or context.resolved.name
        raise RuntimeError(
            "Restore complete, but this backup has no schema-registry baseline. "
            f'Run: omop-config acknowledge-schema-migration --database {database_name} '
            f'--reason "restored from {Path(input_path).expanduser().resolve()}"'
        ) from exc
    for engine in {primary, vocab}:
        engine.dispose()


def restore_database_backup(
    context: MaintenanceContext,
    *,
    input_path: str | Path,
    backup_format: BackupFormat,
    vocab_input_path: str | Path | None = None,
    dry_run: bool = False,
) -> list[BackupResult]:
    """Restore every schema this database entry claims from input_path.
    Runs the restore unless dry_run is True.

    Parameters
    ----------
    context : MaintenanceContext
        Its vocabulary engine is restored from vocab_input_path when given.
        The caller keeps ownership of both engines.
    vocab_input_path : str or Path, optional
        Path to a separate artifact for the vocabulary connection, produced
        by a prior create_database_backup(include_vocab=True) call. Only
        valid when the vocabulary lives on a connection genuinely separate
        from engine's own.
    """
    results = [
        _single_connection_restore(
            context.engine, 
            context.resolved, 
            input_path=input_path, 
            backup_format=backup_format, 
            dry_run=dry_run
        )
    ]

    if vocab_input_path is not None:
        if context.vocab_engine is context.engine:
            raise RuntimeError(
                "vocab_input_path was given but this database's vocabulary is not on a "
                "separate connection; omit vocab_input_path."
            )
        results.append(
            _single_connection_restore(
                context.vocab_engine, 
                context.resolved, 
                input_path=vocab_input_path, 
                backup_format=backup_format, 
                dry_run=dry_run
            )
        )

    if not dry_run:
        _register_restored_schemas(context, input_path)
        if any(
            backend_supports(resolve_backend(engine), "restore_tables")
            for engine in {context.engine, context.vocab_engine}
        ):
            reset_model_sequences(context, vocabulary_included=True)

    return results


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------
app = typer.Typer(
    rich_markup_mode="rich",
    help=f"Manage database backup and restore operations. {backend_support_note('prepare_backup', 'backup_tables')}",
)

@app.command("backup-database")
@omop_command("backup-database", dry_run=True, writes=False)
def backup_database_command(
    conn: MaintenanceContext,
    output_path: str | None = typer.Option(
        None,
        help="Output path for the backup artifact (a directory on DuckDB). Defaults to a timestamped path in the current directory.",
    ),
    backup_format: BackupFormat = typer.Option(
        BackupFormat.CUSTOM,
        help="pg_dump output format on PostgreSQL ('custom': binary .dump; 'plain': SQL .sql). Ignored on DuckDB, which always backs up to a Parquet directory.",
    ),
    include_vocab: bool = typer.Option(
        False,
        "--include-vocab",
        help="Also back up the vocabulary connection, in a second artifact, when it lives on a separate physical database.",
    ),
    dry_run: bool = False,
) -> None:
    """Create a database backup that can be restored with `restore-database`."""
    with console.status("Creating restore-ready database backup..."):
        results = create_database_backup(
            conn,
            output_path=output_path,
            backup_format=backup_format,
            include_vocab=include_vocab,
            dry_run=dry_run,
        )
    for result in results:
        console.print(render_backup_result(result))
        console.print(render_backup_summary(result, dry_run=dry_run))


@app.command("restore-database")
@omop_command("restore-database", dry_run=True, writes=False)
def restore_database_command(
    conn: MaintenanceContext,
    input_path: str = typer.Argument(help="Path to the backup artifact to restore (.dump/.sql file, or a Parquet directory on DuckDB)."),
    backup_format: BackupFormat = typer.Option(
        ...,
        help="Format of the artifact to restore on PostgreSQL; must match the format used when the backup was created. Ignored on DuckDB.",
    ),
    vocab_input_path: str | None = typer.Option(
        None,
        "--vocab-input-path",
        help="Path to a separate vocabulary-connection artifact, produced by `backup-database --include-vocab`.",
    ),
    dry_run: bool = False,
) -> None:
    """Restore a database backup that was created with `backup-database`."""
    with console.status("Restoring database backup..."):
        results = restore_database_backup(
            conn,
            input_path=input_path,
            backup_format=backup_format,
            vocab_input_path=vocab_input_path,
            dry_run=dry_run,
        )
    for result in results:
        console.print(render_restore_result(result))
        console.print(render_restore_summary(result, dry_run=dry_run))
