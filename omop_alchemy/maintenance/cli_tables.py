"""Table operations: ANALYZE statistics refresh, TRUNCATE, and sequence reset commands."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass

import sqlalchemy as sa
import typer

from oa_configurator import (
    autocommit_connection,
    claimed_schema_tags,
    physical_schema_of,
    qualified,
)
from ..backends import resolve_backend, require_backend_support, backend_support_note
from ._cli_utils import Status, dry_label, dry_status, omop_command, resolve_selection
from .context import MaintenanceContext
from .tables import (
    all_table_names,
    TableCategory,
    resolve_maintenance_tables,
    select_maintenance_tables,
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
    schema_tag: str
    operation: str
    status: Status
    detail: str


def analyze_tables(
    context: MaintenanceContext,
    *,
    scope: TableCategory | None = None,
    table_names: tuple[str, ...] | None = None,
    vacuum: bool = False,
    dry_run: bool = False,
) -> list[AnalyzeTableResult]:
    """Run ANALYZE (or VACUUM ANALYZE) on selected ORM-managed tables to refresh planner statistics.

    Runs on every ORM-managed table if both scope and table_names are omitted.
    Each table is analysed on the engine hosting it, one connection per engine.
    """
    if scope is not None and table_names is not None:
        raise RuntimeError("Use either `scope` or `table_names`, not both.")

    selected_tables = resolve_maintenance_tables(scope=scope, table_names=table_names)
    operation = "VACUUM ANALYZE" if vacuum else "ANALYZE"
    results: list[AnalyzeTableResult] = []

    with ExitStack() as stack:
        connections: dict[sa.Engine, sa.Connection] = {}
        for target in context.targets(selected_tables):
            if not target.exists():
                results.append(
                    AnalyzeTableResult(
                        table_name=target.table_name,
                        category=target.table.category,
                        schema_tag=target.schema_tag,
                        operation=operation,
                        status=Status.SKIPPED,
                        detail="table not present in target database",
                    )
                )
                continue

            if not dry_run:
                if target.bind not in connections:
                    connections[target.bind] = stack.enter_context(
                        autocommit_connection(target.bind) if vacuum else target.bind.connect()
                    )
                resolve_backend(target.bind).analyze_table(
                    connections[target.bind], target.table_name, vacuum=vacuum, schema_tag=target.schema_tag
                )

            results.append(
                AnalyzeTableResult(
                    table_name=target.table_name,
                    category=target.table.category,
                    schema_tag=target.schema_tag,
                    operation=operation,
                    status=dry_status(dry_run),
                    detail=dry_label(dry_run, f"{operation.lower()} would run", f"{operation.lower()} completed"),
                )
            )

    return results


# ---------------------------------------------------------------------------
# _truncate_tables
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TruncateTableResult:
    """Outcome of truncating one ORM-managed table, with the pre-truncation row count."""

    table_name: str
    category: TableCategory
    schema_tag: str
    row_count: int | None
    status: Status
    detail: str


def _blocking_foreign_key_references(
    connection: sa.Connection,
    *,
    selected_table_names: set[str],
) -> dict[str, set[str]]:
    """Return tables outside the selection that FK-reference at least one selected table, preventing truncation.

    Scans every schema tag claimed on *connection*'s engine (a vocab table can
    FK-reference a clinical table's PK, or vice versa; likewise an extension
    table). A foreign key never spans two databases, so one engine's scan is
    complete for the tables that engine hosts.
    """
    inspector = sa.inspect(connection)
    blockers: dict[str, set[str]] = {}

    for schema_tag in claimed_schema_tags(connection):
        tag_schema = physical_schema_of(connection, schema_tag=schema_tag)
        for table_name in sorted(all_table_names(connection, schema=tag_schema)):
            if table_name in selected_table_names:
                continue

            for foreign_key in inspector.get_foreign_keys(table_name, schema=tag_schema):
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


def _truncate_tables(
    context: MaintenanceContext,
    *,
    scope: TableCategory | None = None,
    table_names: tuple[str, ...] | None = None,
    restart_identities: bool = False,
    cascade: bool = False,
    dry_run: bool = False,
) -> list[TruncateTableResult]:
    """Truncate selected ORM-managed tables. Raises if non-selected tables hold blocking FK references.

    Each engine's tables are counted, checked for blockers and truncated in
    one transaction on that engine, and in one statement, since PostgreSQL's
    RESTRICT mode requires referencing and referenced tables to be named
    together. No transaction spans the two engines of a split deployment.

    Notes
    -----
    - No provenance guard as the engines were just built in `omop_command`. There is no
    possibility of schema drift between their creation and this command's execution.
    """
    if scope is not None and table_names is not None:
        raise RuntimeError("Use either `scope` or `table_names`, not both.")
    if scope is None and table_names is None:
        raise RuntimeError("Select tables to truncate with `scope` or `table_names`.")

    selected_tables = resolve_maintenance_tables(scope=scope, table_names=table_names)
    groups = context.targets_by_engine(selected_tables)
    for engine in groups:
        require_backend_support(resolve_backend(engine), "truncate_table_batch", "Table truncation")

    row_counts: dict[str, int] = {}
    with ExitStack() as stack:
        connections = {engine: stack.enter_context(engine.begin()) for engine in groups}
        present = {
            engine: [target for target in targets if target.exists()]
            for engine, targets in groups.items()
        }
        for engine, targets in present.items():
            for target in targets:
                row_counts[target.table_name] = int(
                    connections[engine].exec_driver_sql(
                        f"SELECT COUNT(*) FROM {qualified(connections[engine], target.table_name, physical_schema=target.physical_schema)}"
                    ).scalar_one()
                )

        if not dry_run and not cascade:
            blockers: dict[str, set[str]] = {}
            for engine, targets in present.items():
                if targets:
                    blockers |= _blocking_foreign_key_references(
                        connections[engine],
                        selected_table_names={target.table_name for target in targets},
                    )
            if blockers:
                raise RuntimeError(_format_blocking_reference_error(blockers))

        if not dry_run:
            for engine, targets in present.items():
                if targets:
                    resolve_backend(engine).truncate_table_batch(
                        connections[engine],
                        [(target.schema_tag, target.table_name) for target in targets],
                        restart_identities=restart_identities,
                        cascade=cascade,
                    )

    return [
        TruncateTableResult(
            table_name=table.table_name,
            category=table.category,
            schema_tag=table.schema_tag,
            row_count=row_counts[table.table_name],
            status=dry_status(dry_run),
            detail=dry_label(dry_run, "table would be truncated", "table truncated"),
        )
        if table.table_name in row_counts
        else TruncateTableResult(
            table_name=table.table_name,
            category=table.category,
            schema_tag=table.schema_tag,
            row_count=None,
            status=Status.SKIPPED,
            detail="table not present in target database",
        )
        for table in selected_tables
    ]


# ---------------------------------------------------------------------------
# reset_sequences
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SequenceTarget:
    """An ORM-managed table with a single-column integer primary key that owns a PostgreSQL sequence."""

    table_name: str
    category: TableCategory
    schema_tag: str
    pk_column_name: str


@dataclass(frozen=True)
class SequenceResetResult:
    """Outcome of resetting one PostgreSQL sequence to table max + 1."""

    table_name: str
    category: TableCategory
    schema_tag: str
    pk_column_name: str
    sequence_name: str | None
    next_value: int | None
    status: Status
    detail: str


def collect_sequence_targets(
    *,
    vocabulary_included: bool = False,
    vocabulary_only: bool = False,
) -> list[SequenceTarget]:
    """Return ORM-managed tables that have a single integer primary key and therefore own a sequence."""
    targets: list[SequenceTarget] = []
    selected_tables = (
        select_maintenance_tables(
            categories=(TableCategory.VOCABULARY,), require_single_integer_primary_key=True
        )
        if vocabulary_only
        else select_omop_tables(
            vocabulary_included=vocabulary_included,
            require_single_integer_primary_key=True,
        )
    )
    for table in selected_tables:
        pk_column_name = table.single_primary_key_name
        if pk_column_name is None:
            continue
        targets.append(
            SequenceTarget(
                table_name=table.table_name,
                category=table.category,
                schema_tag=table.schema_tag,
                pk_column_name=pk_column_name,
            )
        )
    return targets


def reset_model_sequences(
    context: MaintenanceContext,
    *,
    vocabulary_included: bool = False,
    vocabulary_only: bool = False,
    dry_run: bool = False,
) -> list[SequenceResetResult]:
    """Reset each owned sequence to MAX(pk_column) + 1 to prevent insert conflicts after bulk loads."""
    targets = collect_sequence_targets(vocabulary_included=vocabulary_included, vocabulary_only=vocabulary_only)
    targets_by_engine: dict[sa.Engine, list[SequenceTarget]] = {}
    for target in targets:
        targets_by_engine.setdefault(context.engine_for(target.schema_tag), []).append(target)

    results: list[SequenceResetResult] = []

    for group_engine, group_targets in targets_by_engine.items():
        backend = resolve_backend(group_engine)
        require_backend_support(backend, "find_sequence_name", "Sequence reset")
        inspector = sa.inspect(group_engine)
        with group_engine.begin() as connection:
            for target in group_targets:
                if not inspector.has_table(target.table_name, schema=physical_schema_of(group_engine, schema_tag=target.schema_tag)):
                    continue

                sequence_name = backend.find_sequence_name(
                    connection, target.table_name, target.pk_column_name, schema_tag=target.schema_tag
                )

                if sequence_name is None:
                    results.append(
                        SequenceResetResult(
                            table_name=target.table_name,
                            category=target.category,
                            schema_tag=target.schema_tag,
                            pk_column_name=target.pk_column_name,
                            sequence_name=None,
                            next_value=None,
                            status=Status.SKIPPED,
                            detail="no owned PostgreSQL sequence found",
                        )
                    )
                    continue

                fully_qualified = qualified(
                    connection, target.table_name, physical_schema=physical_schema_of(connection, schema_tag=target.schema_tag)
                )
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
                        schema_tag=target.schema_tag,
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
@omop_command("analyze-tables", dry_run=True, writes=False)
def analyze_tables_command(
    conn: MaintenanceContext,
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
            conn,
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
    conn: MaintenanceContext,
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
            conn,
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
    conn: MaintenanceContext,
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
        results = _truncate_tables(
            conn,
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
