"""Schema subapp: thin shim re-exporting all domain types and wiring seven CLI commands."""

from __future__ import annotations

import typer
from oa_configurator import Resolver, Role, load_stack_config, record_schema_provenance

from ._cli_utils import handle_error, omop_command
from .cli_schema_doctor import (
    DoctorCheck as DoctorCheck,
    DoctorReport as DoctorReport,
    DoctorRecommendation as DoctorRecommendation,
    collect_doctor_report,
)
from .cli_schema_info import (
    CommandSupport as CommandSupport,
    DependencyStatus as DependencyStatus,
    MaintenanceInfo as MaintenanceInfo,
    collect_maintenance_info,
)
from .cli_schema_reconcile import (
    ReconciliationIssue as ReconciliationIssue,
    SchemaReconciliationReport as SchemaReconciliationReport,
    TableReconciliationResult as TableReconciliationResult,
    reconcile_schema,
)
from .cli_schema_rectify import (
    OrphanTablePreview as OrphanTablePreview,
    drop_orphan_schema_tables,
)
from .cli_schema_summary import (
    TableSummaryResult as TableSummaryResult,
    collect_data_summary,
)
from .cli_schema_tables import (
    TableCreationResult as TableCreationResult,
    collect_missing_tables as collect_missing_tables,
    create_missing_tables,
)
from .ui import (
    console,
    render_data_summary_results,
    render_data_summary_summary,
    render_doctor_checks,
    render_doctor_recommendations,
    render_doctor_summary,
    render_foreign_key_validation_issues,
    render_info_command_support,
    render_info_database,
    render_info_dependencies,
    render_info_environment,
    render_info_summary,
    render_reconciliation_issues,
    render_reconciliation_results,
    render_reconciliation_summary,
    render_table_creation_results,
    render_table_creation_summary,
)


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

app = typer.Typer(rich_markup_mode="rich")


@app.command("info")
@omop_command("info", mode_label="inspect")
def info_command(
    conn,
    engine,
    vocabulary_included: bool = typer.Option(
        False,
        "--vocab/--no-vocab",
        help="Include OMOP vocabulary tables in the managed-table count.",
    ),
) -> None:
    """Inspect maintenance CLI readiness, backend compatibility, and current installation state."""
    with console.status("Inspecting maintenance environment..."):
        info = collect_maintenance_info(vocabulary_included=vocabulary_included)
    console.print(render_info_environment(info))
    console.print(render_info_database(info))
    console.print(render_info_dependencies(info))
    console.print(render_info_command_support(info.command_support))
    console.print(render_info_summary(info))


@app.command("doctor")
@omop_command("doctor", mode_label="inspect")
def doctor_command(
    conn,
    engine,
    vocabulary_included: bool = typer.Option(
        False,
        "--vocab/--no-vocab",
        help="Include OMOP vocabulary tables in the selection.",
    ),
    deep: bool = typer.Option(
        False,
        "--deep",
        help="Include heavier checks: FK validation scans every constraint for referential integrity violations.",
    ),
) -> None:
    """Run a read-only maintenance health check across connection readiness, schema drift, and FK state."""
    with console.status("Running maintenance doctor checks..."):
        report = collect_doctor_report(
            engine=engine,
            resolved=conn.resolved,
            db_schema=conn.resolved.schema_name,
            resource_name=conn.resource_name,
            vocabulary_included=vocabulary_included,
            deep=deep,
        )
    console.print(render_info_environment(report.info))
    console.print(render_info_database(report.info))
    console.print(render_doctor_checks(report.checks))
    if deep and report.foreign_key_validation is not None:
        console.print(render_foreign_key_validation_issues(report.foreign_key_validation.violations))
    console.print(render_doctor_recommendations(report.recommendations))
    console.print(render_doctor_summary(report, deep=deep))


@app.command("reconcile-schema")
@omop_command("reconcile-schema", mode_label="inspect")
def reconcile_schema_command(
    conn,
    engine,
    vocabulary_included: bool = typer.Option(
        False,
        "--vocab/--no-vocab",
        help="Include OMOP vocabulary tables in the reconciliation.",
    ),
) -> None:
    """Compare ORM-managed SQLAlchemy metadata against the current target database schema."""
    with console.status("Reconciling ORM metadata against target database schema..."):
        report = reconcile_schema(engine, resolved=conn.resolved, vocabulary_included=vocabulary_included)
    console.print(render_reconciliation_results(report.table_results))
    console.print(render_reconciliation_issues(report.issues))
    console.print(render_reconciliation_summary(report))


@app.command("create-missing-tables")
@omop_command("create-missing-tables", dry_run=True)
def create_missing_tables_command(
    conn,
    engine,
    vocabulary_included: bool = typer.Option(
        True,
        "--vocab/--no-vocab",
        help="Include OMOP vocabulary tables in the selection. Enabled by default.",
    ),
    dry_run: bool = False,
) -> None:
    """Create missing ORM-managed OMOP tables from metadata."""
    vocab_engine = conn.resolved.vocab_engine_for(engine)
    try:
        with console.status("Creating missing tables..."):
            results = create_missing_tables(
                engine,
                vocab_engine=vocab_engine,
                db_schema=conn.resolved.schema_name,
                vocabulary_included=vocabulary_included,
                dry_run=dry_run,
                resolved=conn.resolved,
            )
    finally:
        if vocab_engine is not engine:
            vocab_engine.dispose()
    console.print(render_table_creation_results(results))
    console.print(render_table_creation_summary(results, dry_run=dry_run))


@app.command("data-summary")
@omop_command("data-summary", mode_label="inspect")
def data_summary_command(
    conn,
    engine,
    vocabulary_included: bool = typer.Option(
        False,
        "--vocab/--no-vocab",
        help="Include OMOP vocabulary tables in the summary.",
    ),
    include_missing: bool = typer.Option(
        False,
        "--include-missing",
        help="Also list ORM-managed tables that are absent from the target database.",
    ),
) -> None:
    """Summarise ORM-managed OMOP tables present in the target database."""
    with console.status("Collecting table summary..."):
        results = collect_data_summary(
            engine,
            db_schema=conn.resolved.schema_name,
            vocabulary_included=vocabulary_included,
            existing_only=not include_missing,
        )
    console.print(render_data_summary_results(results))
    console.print(render_data_summary_summary(results))


@app.command("acknowledge-schema-migration")
def acknowledge_schema_migration_command(
    database: str = typer.Option(..., "--database", help="Name of the [databases.*] entry to acknowledge."),
    role: Role = typer.Option(Role.PRIMARY, "--role", help="Logical role whose schema is being acknowledged."),
    new_schema: str = typer.Option(..., "--new-schema", help="Schema to record as the accepted baseline."),
    reason: str = typer.Option(
        ...,
        "--reason",
        help="Free-text justification for this acknowledgment. Mandatory: there is no --yes shortcut.",
    ),
) -> None:
    """Record a schema as the deliberate baseline for a database/role.

    Overwrites any existing provenance row (its prior value moves to
    previous_schema); does not touch the CDM tables themselves.
    """
    try:
        stack = load_stack_config()
        resolved = Resolver(stack).resolve_database(database)
        engine = resolved.create_engine(role=role)
        try:
            with engine.begin() as connection:
                record_schema_provenance(
                    connection, resolved, role=role, new_schema=new_schema, reason=reason
                )
        finally:
            engine.dispose()
    except Exception as exc:
        handle_error(exc)
    console.print(
        f"[green]Acknowledged[/green] {database!r} (role {role.value!r}) -> schema {new_schema!r}."
    )


@app.command("drop-orphan-schema-tables")
def drop_orphan_schema_tables_command(
    database: str = typer.Option(..., "--database", help="Name of the [databases.*] entry providing the connection."),
    schema: str = typer.Option(..., "--schema", help="Orphan schema to inspect/drop tables from."),
    role: Role = typer.Option(Role.PRIMARY, "--role", help="Logical role providing the connection to use."),
    confirm: bool = typer.Option(
        False,
        "--confirm",
        help="Actually drop the previewed tables. Omit to preview only.",
    ),
) -> None:
    """Drop tables physically found in an orphaned schema, after a stack-wide safety check.

    Refuses if the named schema is still the current schema target of any
    configured database/role, not just the one named here. Without
    --confirm, only previews what would be dropped.
    """
    try:
        stack = load_stack_config()
        resolved = Resolver(stack).resolve_database(database)
        engine = resolved.create_engine(role=role)
        try:
            with engine.begin() as connection:
                preview = drop_orphan_schema_tables(
                    connection, stack=stack, orphan_schema=schema, confirm=confirm
                )
        finally:
            engine.dispose()

        if not preview:
            console.print(f"No tables found in schema {schema!r}.")
            return
        for item in preview:
            count = "unknown" if item.approximate_row_count is None else str(item.approximate_row_count)
            verb = "Dropped" if confirm else "Would drop"
            console.print(f"{verb} {schema}.{item.table_name} (~{count} rows)")
        if not confirm:
            console.print("[yellow]Preview only. Re-run with --confirm to actually drop these tables.[/yellow]")
    except Exception as exc:
        handle_error(exc)
