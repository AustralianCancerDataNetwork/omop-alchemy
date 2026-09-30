"""Foreign key trigger management commands for PostgreSQL RI trigger enforcement."""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
import typer

from oa_configurator import ResolvedDatabase, physical_schema_of
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
    schema_tag: str


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
    vocab_engine: sa.Engine,
    vocabulary_included: bool = False,
    vocabulary_only: bool = False,
    resolved: ResolvedDatabase,
) -> list[_FKTableInfo]:
    """Return all ORM-managed tables that participate in at least one FK relationship (outgoing or incoming)."""
    selected_tables = existing_maintenance_tables(
        engine,
        vocab_bindable=vocab_engine,
        vocabulary_included=vocabulary_included,
        categories=(TableCategory.VOCABULARY,) if vocabulary_only else None,
        resolved=resolved,
    )
    tables_by_name = {table.table_name: table for table in selected_tables}
    selected_names = set(tables_by_name)

    incoming_counts = {name: 0 for name in selected_names}
    outgoing_counts = {name: 0 for name in selected_names}

    for table_name in selected_names:
        table_schema_tag = tables_by_name[table_name].schema_tag
        bind = resolved.route_for_schema_tag(table_schema_tag, vocab=vocab_engine, primary=engine)
        foreign_keys = sa.inspect(bind).get_foreign_keys(
            table_name, schema=physical_schema_of(bind, schema_tag=table_schema_tag)
        )
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
                schema_tag=table.schema_tag,
                outgoing_constraint_count=outgoing_count,
                incoming_constraint_count=incoming_count,
            )
        )

    return results


def _collect_strict_validation_failures(
    connection: sa.Connection,
    backend: Backend,
    *,
    targets: list[_FKTableInfo],
) -> dict[str, list[ForeignKeyConstraintViolation]]:
    """Query every FK constraint across targets and return a mapping of table name → violation list.

    Only tables with at least one violation are included in the returned dict.
    Used by manage_foreign_key_triggers (strict=True) and validate_foreign_key_constraints.

    Parameters
    ----------
    targets : list[_FKTableInfo]
        Already selected by the caller and scoped to this connection's own
        physical database. A referred table living on a different
        connection (a genuine cross-server FK) is silently skipped, same as
        it always structurally has to be: no single-connection SQL query
        can validate a constraint spanning two servers.
    """
    inspector = sa.inspect(connection)
    tables_by_name = {table.table_name: table for table in targets}
    selected_names = set(tables_by_name)
    failures: dict[str, list[ForeignKeyConstraintViolation]] = {
        table_name: []
        for table_name in selected_names
    }

    for table_name in sorted(selected_names):
        source_schema_tag = tables_by_name[table_name].schema_tag
        for foreign_key in inspector.get_foreign_keys(
            table_name, schema=physical_schema_of(connection, schema_tag=source_schema_tag)
        ):
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
                source_schema_tag=source_schema_tag,
                referred_schema_tag=tables_by_name[str(referred_table)].schema_tag,
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


def _targets_by_engine(
    targets: list[_FKTableInfo],
    *,
    engine: sa.Engine,
    vocab_engine: sa.Engine,
    resolved: ResolvedDatabase,
) -> list[tuple[sa.Engine, list[_FKTableInfo]]]:
    """Group targets by the physical engine their schema_tag routes to.

    Non-empty groups only, in (engine, vocab_engine) order -- a caller
    opens one connection per group rather than one shared connection for
    every target regardless of which physical database it actually lives on.
    """
    groups = [
        (candidate_engine, [target for target in targets if resolved.route_for_schema_tag(
            target.schema_tag, vocab=vocab_engine, primary=engine
        ) is candidate_engine])
        for candidate_engine in ({engine, vocab_engine} if vocab_engine is not engine else (engine,))
    ]
    return [(candidate_engine, group) for candidate_engine, group in groups if group]


def validate_foreign_key_constraints(
    engine: sa.Engine,
    *,
    vocab_engine: sa.Engine,
    vocabulary_included: bool = False,
    resolved: ResolvedDatabase,
) -> ForeignKeyValidationReport:
    """Count rows that violate each FK constraint and return a full per-table validation report."""
    engine_backends = {
        candidate_engine: resolve_backend(candidate_engine)
        for candidate_engine in ({engine, vocab_engine} if vocab_engine is not engine else (engine,))
    }
    for candidate_backend in engine_backends.values():
        require_backend_support(candidate_backend, "count_fk_violations", "FK constraint validation")

    targets = _collect_fk_info(
        engine,
        vocab_engine=vocab_engine,
        vocabulary_included=vocabulary_included,
        resolved=resolved,
    )

    validation_failures: dict[str, list[ForeignKeyConstraintViolation]] = {}
    for group_engine, group_targets in _targets_by_engine(
        targets, engine=engine, vocab_engine=vocab_engine, resolved=resolved
    ):
        group_backend = engine_backends[group_engine]
        with group_engine.connect() as connection:
            validation_failures.update(
                _collect_strict_validation_failures(connection, group_backend, targets=group_targets)
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
                schema_tag=target.schema_tag,
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
    vocab_engine: sa.Engine,
    enable: bool = False,
    vocabulary_included: bool = False,
    vocabulary_only: bool = False,
    dry_run: bool = False,
    strict: bool = False,
    resolved: ResolvedDatabase,
) -> list[ForeignKeyManagementResult]:
    """Enable or disable RI trigger enforcement. With strict=True, aborts on any FK violation."""
    group_backends = {
        candidate_engine: resolve_backend(candidate_engine)
        for candidate_engine in ({engine, vocab_engine} if vocab_engine is not engine else (engine,))
    }
    for group_backend in group_backends.values():
        require_backend_support(group_backend, "toggle_fk_triggers", "FK trigger management")

    targets = _collect_fk_info(
        engine,
        vocab_engine=vocab_engine,
        vocabulary_included=vocabulary_included,
        vocabulary_only=vocabulary_only,
        resolved=resolved,
    )
    groups = _targets_by_engine(targets, engine=engine, vocab_engine=vocab_engine, resolved=resolved)

    results: list[ForeignKeyManagementResult] = []

    if enable and strict:
        validation_failures: dict[str, list[ForeignKeyConstraintViolation]] = {}
        for group_engine, group_targets in groups:
            with group_engine.connect() as connection:
                validation_failures.update(
                    _collect_strict_validation_failures(
                        connection, group_backends[group_engine], targets=group_targets
                    )
                )
        if validation_failures:
            for target in targets:
                violations = validation_failures.get(target.table_name)
                results.append(
                    ForeignKeyManagementResult(
                        table_name=target.table_name,
                        category=target.category,
                        schema_tag=target.schema_tag,
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
    for group_engine, group_targets in groups:
        group_backend = group_backends[group_engine]
        with group_engine.begin() as connection:
            for target in group_targets:
                if not dry_run:
                    group_backend.toggle_fk_triggers(
                        connection, target.table_name, enable=enable, schema_tag=target.schema_tag
                    )

                results.append(
                    ForeignKeyManagementResult(
                        table_name=target.table_name,
                        category=target.category,
                        schema_tag=target.schema_tag,
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
    vocab_engine: sa.Engine,
    vocabulary_included: bool = False,
    resolved: ResolvedDatabase,
) -> list[ForeignKeyStatusResult]:
    """Query pg_trigger to count disabled vs enabled RI triggers for each participating table."""
    group_backends = {
        candidate_engine: resolve_backend(candidate_engine)
        for candidate_engine in ({engine, vocab_engine} if vocab_engine is not engine else (engine,))
    }
    for candidate_backend in group_backends.values():
        require_backend_support(candidate_backend, "get_fk_trigger_counts", "FK trigger status inspection")

    targets = _collect_fk_info(
        engine,
        vocab_engine=vocab_engine,
        vocabulary_included=vocabulary_included,
        resolved=resolved,
    )
    results: list[ForeignKeyStatusResult] = []

    for group_engine, group_targets in _targets_by_engine(targets, engine=engine, vocab_engine=vocab_engine, resolved=resolved):
        group_backend = group_backends[group_engine]
        with group_engine.connect() as connection:
            for target in group_targets:
                disabled_count, enabled_count = group_backend.get_fk_trigger_counts(
                    connection, target.table_name, schema_tag=target.schema_tag
                )
                results.append(
                    ForeignKeyStatusResult(
                        table_name=target.table_name,
                        category=target.category,
                        schema_tag=target.schema_tag,
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
    vocab_engine,
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
            vocab_engine=vocab_engine,
            enable=False,
            vocabulary_included=vocabulary_included,
            dry_run=dry_run,
            strict=strict,
            resolved=conn.resolved,
        )
    console.print(render_foreign_key_results(results))
    console.print(render_foreign_key_summary(results, dry_run=dry_run))
    console.print(render_foreign_key_note(enable=False, strict=strict))


@app.command("enable")
@omop_command("foreign-keys enable", dry_run=True)
def enable_foreign_keys_command(
    conn,
    engine,
    vocab_engine,
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
            vocab_engine=vocab_engine,
            enable=True,
            vocabulary_included=vocabulary_included,
            dry_run=dry_run,
            strict=strict,
            resolved=conn.resolved,
        )
    console.print(render_foreign_key_results(results))
    console.print(render_foreign_key_summary(results, dry_run=dry_run))
    console.print(render_foreign_key_note(enable=True, strict=strict))


@app.command("status")
@omop_command("foreign-keys status", mode_label="inspect")
def foreign_key_status_command(
    conn,
    engine,
    vocab_engine,
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
            vocab_engine=vocab_engine,
            vocabulary_included=vocabulary_included,
            resolved=conn.resolved,
        )
    console.print(render_foreign_key_status_results(results))
    console.print(render_foreign_key_status_summary(results))


@app.command("validate")
@omop_command("foreign-keys validate", mode_label="inspect")
def foreign_key_validate_command(
    conn,
    engine,
    vocab_engine,
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
            vocab_engine=vocab_engine,
            vocabulary_included=vocabulary_included,
            resolved=conn.resolved,
        )
    console.print(render_foreign_key_validation_results(report.results))
    console.print(render_foreign_key_validation_issues(report.violations))
    console.print(render_foreign_key_validation_summary(report))
