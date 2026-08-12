"""Schema subapp: thin shim re-exporting all domain types and wiring five CLI commands."""

from __future__ import annotations

import typer

from ._cli_utils import omop_command
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
            db_schema=conn.db_schema,
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
        report = reconcile_schema(engine, db_schema=conn.db_schema, vocabulary_included=vocabulary_included)
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
    with console.status("Creating missing tables..."):
        results = create_missing_tables(
            engine,
            db_schema=conn.db_schema,
            vocabulary_included=vocabulary_included,
            dry_run=dry_run,
        )
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
            db_schema=conn.db_schema,
            vocabulary_included=vocabulary_included,
            existing_only=not include_missing,
        )
    console.print(render_data_summary_results(results))
    console.print(render_data_summary_summary(results))
