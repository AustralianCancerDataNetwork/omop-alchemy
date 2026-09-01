"""Foreign key trigger management commands for PostgreSQL RI trigger enforcement."""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
import typer

from ..backends import Backend, resolve_backend, require_backend_support, backend_support_note
from ._cli_utils import Status, dry_label, dry_status, omop_command
from .tables import (
    TableCategory,
    existing_maintenance_tables,
)
from .ui import (
    console,
    render_foreign_key_note,
    render_foreign_key_results,
    render_foreign_key_status_results,
    render_foreign_key_status_summary,
    render_foreign_key_summary,
    render_foreign_key_validation_issues,
    render_foreign_key_validation_results,
    render_foreign_key_validation_summary,
)


@dataclass(frozen=True)
class ForeignKeyBase:
    """Identity and CDM category metadata shared across all foreign key result types."""

    table_name: str
    category: TableCategory


@dataclass(frozen=True)
class _FKTableInfo(ForeignKeyBase):
    """Internal snapshot of a table's outgoing and incoming FK constraint counts, used to drive trigger management."""

    outgoing_constraint_count: int
    incoming_constraint_count: int


@dataclass(frozen=True)
class ForeignKeyManagementResult(_FKTableInfo):
    """Outcome of a FK trigger enable or disable operation for one table."""
    enable: bool
    status: Status
    detail: str


@dataclass(frozen=True)
class ForeignKeyStatusResult(_FKTableInfo):
    """Current FK trigger state for one table: counts of disabled vs enabled PostgreSQL RI triggers."""
    disabled_trigger_count: int
    enabled_trigger_count: int


@dataclass(frozen=True)
class ForeignKeyValidationResult(_FKTableInfo):
    """FK constraint validation outcome for one table, with counts of violating constraints and rows."""
    violating_constraint_count: int
    violating_row_count: int
    status: Status
    detail: str


@dataclass(frozen=True)
class ForeignKeyConstraintViolation:
    """A single FK constraint that has referential integrity violations, with the violation row count."""

    source_table_name: str
    referred_table_name: str
    constraint_name: str
    violation_count: int


@dataclass(frozen=True)
class ForeignKeyValidationReport:
    """Complete FK validation report: per-table results and the full flat list of violations."""

    results: tuple[ForeignKeyValidationResult, ...]
    violations: tuple[ForeignKeyConstraintViolation, ...]


def _collect_fk_info(
    engine: sa.Engine,
    *,
    db_schema: str | None = None,
    vocabulary_included: bool = False,
) -> list[_FKTableInfo]:
    """Return all ORM-managed tables that participate in at least one FK relationship (outgoing or incoming)."""
    inspector = sa.inspect(engine)

    selected_tables = existing_maintenance_tables(
        inspector,
        db_schema=db_schema,
        vocabulary_included=vocabulary_included,
    )
    selected_names = {table.table_name for table in selected_tables}

    incoming_counts = {name: 0 for name in selected_names}
    outgoing_counts = {name: 0 for name in selected_names}

    for table_name in selected_names:
        foreign_keys = inspector.get_foreign_keys(table_name, schema=db_schema)
        relevant_foreign_keys = [
            foreign_key
            for foreign_key in foreign_keys
            if foreign_key.get("referred_table") in selected_names
        ]
        outgoing_counts[table_name] = len(relevant_foreign_keys)
        for foreign_key in relevant_foreign_keys:
            referred_table = foreign_key.get("referred_table")
            if referred_table is not None:
                incoming_counts[referred_table] += 1

    results: list[_FKTableInfo] = []
    for table in selected_tables:
        if table.table_name not in selected_names:
            continue
        outgoing_count = outgoing_counts[table.table_name]
        incoming_count = incoming_counts[table.table_name]
        if outgoing_count == 0 and incoming_count == 0:
            continue
        results.append(
            _FKTableInfo(
                table_name=table.table_name,
                category=table.category,
                outgoing_constraint_count=outgoing_count,
                incoming_constraint_count=incoming_count,
            )
        )

    return results


def _collect_strict_validation_failures(
    connection: sa.Connection,
    backend: Backend,
    *,
    db_schema: str | None,
    vocabulary_included: bool,
) -> dict[str, list[ForeignKeyConstraintViolation]]:
    """Query every FK constraint across selected tables and return a mapping of table name → violation list.

    Only tables with at least one violation are included in the returned dict.
    Used by manage_foreign_key_triggers (strict=True) and validate_foreign_key_constraints.
    """
    inspector = sa.inspect(connection)
    selected_tables = existing_maintenance_tables(
        inspector,
        db_schema=db_schema,
        vocabulary_included=vocabulary_included,
    )
    selected_names = {table.table_name for table in selected_tables}
    failures: dict[str, list[ForeignKeyConstraintViolation]] = {
        table_name: []
        for table_name in selected_names
    }

    for table_name in sorted(selected_names):
        for foreign_key in inspector.get_foreign_keys(table_name, schema=db_schema):
            referred_table = foreign_key.get("referred_table")
            constrained_columns = foreign_key.get("constrained_columns") or []
            referred_columns = foreign_key.get("referred_columns") or []

            if (
                referred_table not in selected_names
                or len(constrained_columns) == 0
                or len(constrained_columns) != len(referred_columns)
            ):
                continue

            violation_count = backend.count_fk_violations(
                connection,
                table_name,
                str(referred_table),
                list(constrained_columns),
                list(referred_columns),
            )

            if violation_count == 0:
                continue

            failures[table_name].append(
                ForeignKeyConstraintViolation(
                    source_table_name=table_name,
                    referred_table_name=str(referred_table),
                    constraint_name=foreign_key.get("name") or "(unnamed constraint)",
                    violation_count=violation_count,
                )
            )

    return {
        table_name: violations
        for table_name, violations in failures.items()
        if violations
    }


def _fk_violation_detail(
    violations: list[ForeignKeyConstraintViolation],
    *,
    strict_abort: bool = False,
) -> str:
    """Format a list of FK violations into a human-readable detail string."""
    constraint_summary = ", ".join(
        f"{v.constraint_name} ({v.violation_count})"
        for v in violations[:3]
    )
    if len(violations) > 3:
        constraint_summary = f"{constraint_summary}, +{len(violations) - 3} more"
    total = sum(v.violation_count for v in violations)
    prefix = "Strict validation failed; no FK triggers were enabled. " if strict_abort else ""
    return f"{prefix}{total} violating row(s) across {len(violations)} constraint(s): {constraint_summary}"


def validate_foreign_key_constraints(
    engine: sa.Engine,
    *,
    db_schema: str | None = None,
    vocabulary_included: bool = False,
) -> ForeignKeyValidationReport:
    """Count rows that violate each FK constraint and return a full per-table validation report."""
    backend = resolve_backend(engine)
    require_backend_support(backend, "count_fk_violations", "FK constraint validation")

    targets = _collect_fk_info(
        engine,
        db_schema=db_schema,
        vocabulary_included=vocabulary_included,
    )

    with engine.connect() as connection:
        validation_failures = _collect_strict_validation_failures(
            connection,
            backend,
            db_schema=db_schema,
            vocabulary_included=vocabulary_included,
        )

    results: list[ForeignKeyValidationResult] = []
    all_violations: list[ForeignKeyConstraintViolation] = []

    for target in targets:
        violations = validation_failures.get(target.table_name, [])
        violating_constraint_count = len(violations)
        violating_row_count = sum(violation.violation_count for violation in violations)
        results.append(
            ForeignKeyValidationResult(
                table_name=target.table_name,
                category=target.category,
                outgoing_constraint_count=target.outgoing_constraint_count,
                incoming_constraint_count=target.incoming_constraint_count,
                violating_constraint_count=violating_constraint_count,
                violating_row_count=violating_row_count,
                status=Status.FAILED if violations else Status.PASSED,
                detail=(
                    _fk_violation_detail(violations)
                    if violations
                    else "No FK violations found for this table."
                ),
            )
        )
        all_violations.extend(violations)

    all_violations.sort(
        key=lambda violation: (violation.source_table_name, violation.constraint_name)
    )
    return ForeignKeyValidationReport(
        results=tuple(results),
        violations=tuple(all_violations),
    )


def manage_foreign_key_triggers(
    engine: sa.Engine,
    *,
    enable: bool = False,
    db_schema: str | None = None,
    vocabulary_included: bool = False,
    dry_run: bool = False,
    strict: bool = False,
) -> list[ForeignKeyManagementResult]:
    """Enable or disable RI trigger enforcement. With strict=True, aborts on any FK violation."""
    backend = resolve_backend(engine)
    require_backend_support(backend, "toggle_fk_triggers", "FK trigger management")

    targets = _collect_fk_info(
        engine,
        db_schema=db_schema,
        vocabulary_included=vocabulary_included,
    )

    results: list[ForeignKeyManagementResult] = []
    with engine.begin() as connection:
        if enable and strict:
            validation_failures = _collect_strict_validation_failures(
                connection,
                backend,
                db_schema=db_schema,
                vocabulary_included=vocabulary_included,
            )
            if validation_failures:
                for target in targets:
                    violations = validation_failures.get(target.table_name)
                    results.append(
                        ForeignKeyManagementResult(
                            table_name=target.table_name,
                            category=target.category,
                            outgoing_constraint_count=target.outgoing_constraint_count,
                            incoming_constraint_count=target.incoming_constraint_count,
                            enable=enable,
                            status=Status.FAILED if violations else Status.SKIPPED,
                            detail=(
                                _fk_violation_detail(violations, strict_abort=True)
                                if violations
                                else "Strict validation failed on other tables; no FK triggers were enabled."
                            ),
                        )
                    )
                return results
        

        fk_planned_applied: dict[tuple[bool, bool], tuple[str, str]] = {
            (False, False): ("FK trigger enforcement would be disabled", "FK trigger enforcement disabled"),
            (True,  False): ("FK trigger enforcement would be enabled", "FK trigger enforcement enabled"),
            (True,  True):  ("Strict FK validation passed; trigger enforcement would be enabled", "Strict FK validation passed; trigger enforcement enabled"),
        }
        for target in targets:
            if not dry_run:
                backend.toggle_fk_triggers(connection, target.table_name, enable=enable)

            results.append(
                ForeignKeyManagementResult(
                    table_name=target.table_name,
                    category=target.category,
                    outgoing_constraint_count=target.outgoing_constraint_count,
                    incoming_constraint_count=target.incoming_constraint_count,
                    enable=enable,
                    status=dry_status(dry_run),
                    detail=dry_label(dry_run, *fk_planned_applied[(enable, enable and strict)]),
                )
            )

    return results


def collect_foreign_key_trigger_status(
    engine: sa.Engine,
    *,
    db_schema: str | None = None,
    vocabulary_included: bool = False,
) -> list[ForeignKeyStatusResult]:
    """Query pg_trigger to count disabled vs enabled RI triggers for each participating table."""
    backend = resolve_backend(engine)
    require_backend_support(backend, "get_fk_trigger_counts", "FK trigger status inspection")

    targets = _collect_fk_info(
        engine,
        db_schema=db_schema,
        vocabulary_included=vocabulary_included,
    )
    results: list[ForeignKeyStatusResult] = []

    with engine.connect() as connection:
        for target in targets:
            disabled_count, enabled_count = backend.get_fk_trigger_counts(
                connection, target.table_name
            )
            results.append(
                ForeignKeyStatusResult(
                    table_name=target.table_name,
                    category=target.category,
                    disabled_trigger_count=disabled_count,
                    enabled_trigger_count=enabled_count,
                    outgoing_constraint_count=target.outgoing_constraint_count,
                    incoming_constraint_count=target.incoming_constraint_count,
                )
            )

    return results


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

app = typer.Typer(
    help=f"Manage RI trigger enforcement for OMOP tables. {backend_support_note('toggle_fk_triggers')}",
    rich_markup_mode="rich",
)

@app.command("disable")
@omop_command("foreign-keys disable", dry_run=True)
def disable_foreign_keys_command(
    conn,
    engine,
    vocabulary_included: bool = typer.Option(
        False,
        "--vocab/--no-vocab",
        help="Include OMOP vocabulary tables in the selection.",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Validate all FK relationships and report violations before disabling trigger enforcement.",
    ),
    dry_run: bool = False,
) -> None:
    """Disable PostgreSQL RI trigger enforcement for all participating OMOP tables."""
    with console.status("Managing PostgreSQL foreign key trigger enforcement..."):
        results = manage_foreign_key_triggers(
            engine,
            enable=False,
            db_schema=conn.db_schema,
            vocabulary_included=vocabulary_included,
            dry_run=dry_run,
            strict=strict,
        )
    console.print(render_foreign_key_results(results))
    console.print(render_foreign_key_summary(results, dry_run=dry_run))
    console.print(render_foreign_key_note(enable=False, strict=strict))


@app.command("enable")
@omop_command("foreign-keys enable", dry_run=True)
def enable_foreign_keys_command(
    conn,
    engine,
    vocabulary_included: bool = typer.Option(
        False,
        "--vocab/--no-vocab",
        help="Include OMOP vocabulary tables in the selection.",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Validate all FK relationships before enabling trigger enforcement; aborts if any violations are found.",
    ),
    dry_run: bool = False,
) -> None:
    """Re-enable PostgreSQL RI trigger enforcement. Use --strict to abort if any violations exist first."""
    status_msg = (
        "Validating and enabling PostgreSQL foreign key trigger enforcement..."
        if strict
        else "Managing PostgreSQL foreign key trigger enforcement..."
    )
    with console.status(status_msg):
        results = manage_foreign_key_triggers(
            engine,
            enable=True,
            db_schema=conn.db_schema,
            vocabulary_included=vocabulary_included,
            dry_run=dry_run,
            strict=strict,
        )
    console.print(render_foreign_key_results(results))
    console.print(render_foreign_key_summary(results, dry_run=dry_run))
    console.print(render_foreign_key_note(enable=True, strict=strict))


@app.command("status")
@omop_command("foreign-keys status", mode_label="inspect")
def foreign_key_status_command(
    conn,
    engine,
    vocabulary_included: bool = typer.Option(
        False,
        "--vocab/--no-vocab",
        help="Include OMOP vocabulary tables in the selection.",
    ),
) -> None:
    """Show the current enabled/disabled state of RI triggers for each participating OMOP table."""
    with console.status("Inspecting foreign key trigger status..."):
        results = collect_foreign_key_trigger_status(
            engine,
            db_schema=conn.db_schema,
            vocabulary_included=vocabulary_included,
        )
    console.print(render_foreign_key_status_results(results))
    console.print(render_foreign_key_status_summary(results))


@app.command("validate")
@omop_command("foreign-keys validate", mode_label="inspect")
def foreign_key_validate_command(
    conn,
    engine,
    vocabulary_included: bool = typer.Option(
        False,
        "--vocab/--no-vocab",
        help="Include OMOP vocabulary tables in the selection.",
    ),
) -> None:
    """Validate FK constraints on selected tables and report any rows that violate referential integrity."""
    with console.status("Validating selected foreign key relationships..."):
        report = validate_foreign_key_constraints(
            engine,
            db_schema=conn.db_schema,
            vocabulary_included=vocabulary_included,
        )
    console.print(render_foreign_key_validation_results(report.results))
    console.print(render_foreign_key_validation_issues(report.violations))
    console.print(render_foreign_key_validation_summary(report))
