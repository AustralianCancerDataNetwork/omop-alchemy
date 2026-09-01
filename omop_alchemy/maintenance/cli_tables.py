"""Table operations: ANALYZE statistics refresh, TRUNCATE, and sequence reset commands."""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
import typer

from oa_configurator import autocommit_connection, qualified
from ..backends import resolve_backend, require_backend_support, backend_support_note
from ._cli_utils import Status, dry_label, dry_status, omop_command, resolve_selection
from .tables import (
    TableCategory,
    resolve_maintenance_tables,
    select_omop_tables,
)
from .ui import (
    console,
    render_analyze_note,
    render_analyze_results,
    render_analyze_summary,
    render_error,
    render_sequence_reset_results,
    render_sequence_reset_summary,
    render_truncate_note,
    render_truncate_results,
    render_truncate_summary,
)


# ---------------------------------------------------------------------------
# analyze_tables
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AnalyzeTableResult:
    """Outcome of an ANALYZE or VACUUM ANALYZE operation for one ORM-managed table."""

    table_name: str
    category: TableCategory
    operation: str
    status: Status
    detail: str


def analyze_tables(
    engine: sa.Engine,
    *,
    db_schema: str | None = None,
    scope: TableCategory | None = None,
    table_names: tuple[str, ...] | None = None,
    vacuum: bool = False,
    dry_run: bool = False,
) -> list[AnalyzeTableResult]:
    """Run ANALYZE (or VACUUM ANALYZE) on selected ORM-managed tables to refresh planner statistics.

    Runs on every ORM-managed table if both scope and table_names are omitted.
    """
    if scope is not None and table_names is not None:
        raise RuntimeError("Use either `scope` or `table_names`, not both.")

    backend = resolve_backend(engine)
    selected_tables = resolve_maintenance_tables(scope=scope, table_names=table_names)
    inspector = sa.inspect(engine)
    operation = "VACUUM ANALYZE" if vacuum else "ANALYZE"
    results: list[AnalyzeTableResult] = []

    connection_factory = autocommit_connection(engine) if vacuum else engine.connect()

    with connection_factory as connection:
        for maintenance_table in selected_tables:
            if not inspector.has_table(maintenance_table.table_name, schema=db_schema):
                results.append(
                    AnalyzeTableResult(
                        table_name=maintenance_table.table_name,
                        category=maintenance_table.category,
                        operation=operation,
                        status=Status.SKIPPED,
                        detail="table not present in target database",
                    )
                )
                continue

            if not dry_run:
                backend.analyze_table(connection, maintenance_table.table_name, vacuum=vacuum)

            results.append(
                AnalyzeTableResult(
                    table_name=maintenance_table.table_name,
                    category=maintenance_table.category,
                    operation=operation,
                    status=dry_status(dry_run),
                    detail=dry_label(dry_run, f"{operation.lower()} would run", f"{operation.lower()} completed"),
                )
            )

    return results


# ---------------------------------------------------------------------------
# truncate_tables
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TruncateTableResult:
    """Outcome of truncating one ORM-managed table, with the pre-truncation row count."""

    table_name: str
    category: TableCategory
    row_count: int | None
    status: Status
    detail: str


def _blocking_foreign_key_references(
    inspector: sa.Inspector,
    *,
    db_schema: str | None,
    selected_table_names: set[str],
) -> dict[str, set[str]]:
    """Return tables outside the selection that FK-reference at least one selected table, preventing truncation."""
    blockers: dict[str, set[str]] = {}

    for table_name in inspector.get_table_names(schema=db_schema):
        if table_name in selected_table_names:
            continue

        for foreign_key in inspector.get_foreign_keys(table_name, schema=db_schema):
            referred_table = foreign_key.get("referred_table")
            if referred_table not in selected_table_names:
                continue
            blockers.setdefault(str(referred_table), set()).add(table_name)

    return blockers


def _format_blocking_reference_error(blockers: dict[str, set[str]]) -> str:
    """Format a human-readable error message listing which external tables are blocking truncation."""
    blocker_parts = [
        f"{table_name} <- {', '.join(sorted(referencing_tables))}"
        for table_name, referencing_tables in sorted(blockers.items())
    ]
    preview = "; ".join(blocker_parts[:5])
    if len(blocker_parts) > 5:
        preview = f"{preview}; +{len(blocker_parts) - 5} more"

    return (
        "Truncation would be blocked by foreign key references from tables outside the current selection. "
        f"Blocking references: {preview}. "
        "Use `--cascade`, expand the table selection, or disable foreign key trigger enforcement first."
    )


def truncate_tables(
    engine: sa.Engine,
    *,
    db_schema: str | None = None,
    scope: TableCategory | None = None,
    table_names: tuple[str, ...] | None = None,
    restart_identities: bool = False,
    cascade: bool = False,
    dry_run: bool = False,
) -> list[TruncateTableResult]:
    """Truncate selected ORM-managed tables. Raises if non-selected tables hold blocking FK references."""
    if scope is not None and table_names is not None:
        raise RuntimeError("Use either `scope` or `table_names`, not both.")
    if scope is None and table_names is None:
        raise RuntimeError("Select tables to truncate with `scope` or `table_names`.")

    backend = resolve_backend(engine)
    require_backend_support(backend, "truncate_table_batch", "Table truncation")
    selected_tables = resolve_maintenance_tables(scope=scope, table_names=table_names)
    inspector = sa.inspect(engine)
    results: list[TruncateTableResult] = []
    existing_tables: list[str] = []

    with engine.begin() as connection:
        for maintenance_table in selected_tables:
            if not inspector.has_table(maintenance_table.table_name, schema=db_schema):
                results.append(
                    TruncateTableResult(
                        table_name=maintenance_table.table_name,
                        category=maintenance_table.category,
                        row_count=None,
                        status=Status.SKIPPED,
                        detail="table not present in target database",
                    )
                )
                continue

            row_count = int(
                connection.exec_driver_sql(
                    f"SELECT COUNT(*) FROM {qualified(connection, maintenance_table.table_name)}"
                ).scalar_one()
            )
            existing_tables.append(maintenance_table.table_name)
            results.append(
                TruncateTableResult(
                    table_name=maintenance_table.table_name,
                    category=maintenance_table.category,
                    row_count=row_count,
                    status=dry_status(dry_run),
                    detail=dry_label(dry_run, "table would be truncated", "table truncated"),
                )
            )

        if existing_tables and not dry_run and not cascade:
            blockers = _blocking_foreign_key_references(
                inspector,
                db_schema=db_schema,
                selected_table_names=set(existing_tables),
            )
            if blockers:
                raise RuntimeError(_format_blocking_reference_error(blockers))

        if existing_tables and not dry_run:
            backend.truncate_table_batch(
                connection,
                existing_tables,
                restart_identities=restart_identities,
                cascade=cascade,
            )

    return results


# ---------------------------------------------------------------------------
# reset_sequences
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SequenceTarget:
    """An ORM-managed table with a single-column integer primary key that owns a PostgreSQL sequence."""

    table_name: str
    category: TableCategory
    pk_column_name: str


@dataclass(frozen=True)
class SequenceResetResult:
    """Outcome of resetting one PostgreSQL sequence to table max + 1."""

    table_name: str
    category: TableCategory
    pk_column_name: str
    sequence_name: str | None
    next_value: int | None
    status: Status
    detail: str


def collect_sequence_targets(
    *,
    vocabulary_included: bool = False,
) -> list[SequenceTarget]:
    """Return ORM-managed tables that have a single integer primary key and therefore own a sequence."""
    targets: list[SequenceTarget] = []
    for table in select_omop_tables(
        vocabulary_included=vocabulary_included,
        require_single_integer_primary_key=True,
    ):
        pk_column_name = table.single_primary_key_name
        if pk_column_name is None:
            continue
        targets.append(
            SequenceTarget(
                table_name=table.table_name,
                category=table.category,
                pk_column_name=pk_column_name,
            )
        )
    return targets


def reset_model_sequences(
    engine: sa.Engine,
    *,
    db_schema: str | None = None,
    vocabulary_included: bool = False,
    dry_run: bool = False,
) -> list[SequenceResetResult]:
    """Reset each owned sequence to MAX(pk_column) + 1 to prevent insert conflicts after bulk loads."""
    backend = resolve_backend(engine)
    require_backend_support(backend, "find_sequence_name", "Sequence reset")
    inspector = sa.inspect(engine)
    targets = collect_sequence_targets(vocabulary_included=vocabulary_included)
    results: list[SequenceResetResult] = []

    with engine.begin() as connection:
        for target in targets:
            if not inspector.has_table(target.table_name, schema=db_schema):
                continue

            sequence_name = backend.find_sequence_name(
                connection, target.table_name, target.pk_column_name
            )

            if sequence_name is None:
                results.append(
                    SequenceResetResult(
                        table_name=target.table_name,
                        category=target.category,
                        pk_column_name=target.pk_column_name,
                        sequence_name=None,
                        next_value=None,
                        status=Status.SKIPPED,
                        detail="no owned PostgreSQL sequence found",
                    )
                )
                continue

            fully_qualified = qualified(connection, target.table_name)
            current_max = connection.execute(
                sa.text(
                    f"SELECT COALESCE(MAX({target.pk_column_name}), 0) "
                    f"FROM {fully_qualified}"
                )
            ).scalar_one()
            next_value = int(current_max) + 1

            if not dry_run:
                backend.set_sequence_value(connection, sequence_name, next_value)

            results.append(
                SequenceResetResult(
                    table_name=target.table_name,
                    category=target.category,
                    pk_column_name=target.pk_column_name,
                    sequence_name=sequence_name,
                    next_value=next_value,
                    status=dry_status(dry_run, applied=Status.RESET),
                    detail=dry_label(dry_run, "sequence would be reset from table max + 1", "sequence reset from table max + 1"),
                )
            )

    return results


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

app = typer.Typer(rich_markup_mode="rich", help="Manage Database Tables: analyze, truncate, and reset sequences",)

@app.command("analyze-tables")
@omop_command("analyze-tables", dry_run=True)
def analyze_tables_command(
    conn,
    engine,
    scope: TableCategory | None = typer.Option(
        None,
        "--scope",
        help="CDM category scope to analyze (e.g. 'clinical', 'vocabulary'). Defaults to all ORM-managed tables when omitted.",
        case_sensitive=False,
    ),
    table: list[str] | None = typer.Option(
        None,
        "--table",
        help="Specific ORM-managed table name to analyze. Repeat to target multiple tables.",
    ),
    vacuum: bool = typer.Option(
        False,
        "--vacuum",
        help="Use VACUUM ANALYZE instead of plain ANALYZE to also reclaim dead tuples. Not available on all backends.",
    ),
    dry_run: bool = False,
) -> None:
    """Analyse selected ORM-managed tables to update planner statistics."""
    resolved_scope, resolved_tables = resolve_selection(scope=scope, tables=table)
    with console.status("Refreshing planner statistics for selected tables..."):
        results = analyze_tables(
            engine,
            db_schema=conn.db_schema,
            scope=resolved_scope,
            table_names=resolved_tables,
            vacuum=vacuum,
            dry_run=dry_run,
        )
    console.print(render_analyze_results(results))
    console.print(render_analyze_summary(results, dry_run=dry_run))
    console.print(render_analyze_note())


@app.command(
    "reset-sequences",
    help=f"Reset each owned sequence to MAX(pk) + 1 to prevent insert conflicts after bulk loads. {backend_support_note('find_sequence_name')}",
)
@omop_command("reset-sequences", dry_run=True)
def reset_sequences_command(
    conn,
    engine,
    vocabulary_included: bool = typer.Option(
        False,
        "--vocab/--no-vocab",
        help="Include OMOP vocabulary tables in the selection.",
    ),
    dry_run: bool = False,
) -> None:
    """Reset each owned sequence to MAX(pk) + 1 to prevent insert conflicts after bulk loads."""
    with console.status("Resetting PostgreSQL sequences..."):
        results = reset_model_sequences(
            engine,
            db_schema=conn.db_schema,
            vocabulary_included=vocabulary_included,
            dry_run=dry_run,
        )
    console.print(render_sequence_reset_results(results))
    console.print(render_sequence_reset_summary(results, dry_run=dry_run))


@app.command(
    "truncate-tables",
    help=f"Truncate selected ORM-managed OMOP tables; aborts if external FK references would block unless --cascade is set. {backend_support_note('truncate_table_batch')}",
)
@omop_command("truncate-tables", dry_run=True)
def truncate_tables_command(
    conn,
    engine,
    scope: TableCategory | None = typer.Option(
        None,
        "--scope",
        help="CDM category scope to truncate (e.g. 'clinical', 'vocabulary'). Must specify scope or --table.",
        case_sensitive=False,
    ),
    table: list[str] | None = typer.Option(
        None,
        "--table",
        help="Specific ORM-managed table name to truncate. Repeat to target multiple tables.",
    ),
    restart_identities: bool = typer.Option(
        False,
        "--restart-identities",
        help="Reset owned sequences to 1 after truncation (TRUNCATE ... RESTART IDENTITY).",
    ),
    cascade: bool = typer.Option(
        False,
        "--cascade",
        help="Automatically truncate dependent tables via PostgreSQL CASCADE. Use with care.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Confirm the destructive operation. Required when not using --dry-run.",
    ),
    dry_run: bool = False,
) -> None:
    """Truncate selected ORM-managed OMOP tables. Aborts if external FK references would block unless --cascade is set."""
    resolved_scope, resolved_tables = resolve_selection(scope=scope, tables=table)
    if resolved_scope is None and resolved_tables is None:
        console.print(
            render_error("Select tables to truncate with `--scope` or one or more `--table` values.")
        )
        raise typer.Exit(code=1)
    if not dry_run and not yes:
        console.print(
            render_error("Truncation is destructive. Re-run with `--yes`, or use `--dry-run` first.")
        )
        raise typer.Exit(code=1)
    with console.status("Truncating selected tables..."):
        results = truncate_tables(
            engine,
            db_schema=conn.db_schema,
            scope=resolved_scope,
            table_names=resolved_tables,
            restart_identities=restart_identities,
            cascade=cascade,
            dry_run=dry_run,
        )
    console.print(render_truncate_results(results))
    console.print(
        render_truncate_summary(
            results,
            dry_run=dry_run,
            restart_identities=restart_identities,
            cascade=cascade,
        )
    )
    console.print(render_truncate_note())
