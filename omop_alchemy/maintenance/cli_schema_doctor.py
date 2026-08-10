"""Health check domain: connection readiness, schema drift, FK trigger state, and FK validation checks with prioritised recommendations."""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa

from omop_alchemy.backends.resolve import SupportedDialect

from ._cli_utils import Status
from .cli_foreign_keys import (
    ForeignKeyStatusResult,
    ForeignKeyValidationReport,
    collect_foreign_key_trigger_status,
    validate_foreign_key_constraints,
)
from .cli_schema_info import (
    MaintenanceInfo,
    collect_maintenance_info,
)
from .cli_schema_reconcile import (
    SchemaReconciliationReport,
    is_blocking_issue,
    reconcile_schema,
)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DoctorCheck:
    """Result of a single named maintenance health check (e.g. 'managed tables', 'schema drift')."""

    name: str
    status: Status
    detail: str


@dataclass(frozen=True)
class DoctorRecommendation:
    """Actionable recommendation derived from health check results, with an optional CLI command hint."""

    status: Status
    summary: str
    action: str | None


@dataclass(frozen=True)
class DoctorReport:
    """Complete doctor report: health checks, prioritised recommendations, and optional deep-inspection data."""

    info: MaintenanceInfo
    checks: tuple[DoctorCheck, ...]
    recommendations: tuple[DoctorRecommendation, ...]
    reconciliation: SchemaReconciliationReport | None
    foreign_key_status: tuple[ForeignKeyStatusResult, ...] | None
    foreign_key_validation: ForeignKeyValidationReport | None


def _build_recommendations(
    *,
    info: MaintenanceInfo,
    reconciliation: SchemaReconciliationReport | None,
    foreign_key_status: tuple[ForeignKeyStatusResult, ...] | None,
    foreign_key_validation: ForeignKeyValidationReport | None,
) -> tuple[DoctorRecommendation, ...]:
    """Derive a prioritised list of actionable recommendations from the doctor check results."""
    recommendations: list[DoctorRecommendation] = []

    if not info.connection_ready:
        recommendations.append(
            DoctorRecommendation(
                status=Status.FAILED,
                summary="Database connection is not ready for maintenance operations.",
                action="Check the engine configuration, backend driver, and target database reachability.",
            )
        )
        return tuple(recommendations)

    if info.missing_table_count:
        recommendations.append(
            DoctorRecommendation(
                status=Status.WARNING,
                summary=f"{info.missing_table_count} ORM-managed table(s) are missing from the target database.",
                action="Run `omop-alchemy create-missing-tables` before attempting bulk operations.",
            )
        )

    if reconciliation is not None:
        blocking_issue_count = sum(
            1 for issue in reconciliation.issues if is_blocking_issue(issue)
        )
        if blocking_issue_count:
            recommendations.append(
                DoctorRecommendation(
                    status=Status.WARNING,
                    summary=f"Schema reconciliation found {blocking_issue_count} difference(s) against ORM metadata.",
                    action="Review `omop-alchemy reconcile-schema` output before continuing with ETL or maintenance work.",
                )
            )

    if foreign_key_status is not None and any(
        item.disabled_trigger_count > 0 for item in foreign_key_status
    ):
        recommendations.append(
            DoctorRecommendation(
                status=Status.WARNING,
                summary="Some PostgreSQL RI triggers are currently disabled.",
                action="If loading is complete, run `omop-alchemy foreign-keys validate` and then `omop-alchemy foreign-keys enable --strict`.",
            )
        )

    if (
        foreign_key_validation is not None
        and any(
            result.status == Status.FAILED
            for result in foreign_key_validation.results
        )
    ):
        recommendations.append(
            DoctorRecommendation(
                status=Status.FAILED,
                summary="Foreign key validation found violating rows.",
                action="Fix the reported rows, then rerun `omop-alchemy foreign-keys enable --strict`.",
            )
        )

    if info.backend == SupportedDialect.POSTGRESQL and info.pg_dump_path is None:
        recommendations.append(
            DoctorRecommendation(
                status=Status.WARNING,
                summary="`pg_dump` is not on PATH, so backup-database is unavailable from this machine.",
                action="Install PostgreSQL client tools on the machine running `omop-alchemy`.",
            )
        )

    if (
        info.backend == SupportedDialect.POSTGRESQL
        and info.pg_restore_path is None
        and info.psql_path is None
    ):
        recommendations.append(
            DoctorRecommendation(
                status=Status.WARNING,
                summary="Neither `pg_restore` nor `psql` is on PATH, so restore-database is unavailable from this machine.",
                action="Install PostgreSQL client tools on the machine running `omop-alchemy`.",
            )
        )

    if not recommendations:
        recommendations.append(
            DoctorRecommendation(
                status=Status.PASSED,
                summary="No obvious maintenance blockers were detected.",
                action=None,
            )
        )

    return tuple(recommendations)


def collect_doctor_report(
    *,
    engine: sa.engine.Engine,
    vocabulary_included: bool = True,
    deep: bool = False,
) -> DoctorReport:
    """Run all maintenance health checks and return a prioritised report with recommendations.

    Parameters
    ----------
    engine : sa.engine.Engine
        Already-resolved CDM engine (e.g. from the ``@omop_command`` decorator),
        reused for the deep checks below instead of re-resolving config.
    """
    info = collect_maintenance_info(vocabulary_included=vocabulary_included)

    checks = [
        DoctorCheck(
            name="connection",
            status=Status.PASSED if info.connection_ready else Status.FAILED,
            detail=(
                "Target database connection succeeded."
                if info.connection_ready
                else info.connection_error or info.engine_error or "Connection could not be established."
            ),
        )
    ]

    reconciliation: SchemaReconciliationReport | None = None
    foreign_key_status: tuple[ForeignKeyStatusResult, ...] | None = None
    foreign_key_validation: ForeignKeyValidationReport | None = None

    if info.connection_ready:
        db_schema = info.db_schema
        missing_table_count = info.missing_table_count or 0
        checks.append(
            DoctorCheck(
                name="managed tables",
                status=Status.PASSED if missing_table_count == 0 else Status.WARNING,
                detail=(
                    "All selected ORM-managed tables exist."
                    if missing_table_count == 0
                    else f"{missing_table_count} selected table(s) are missing."
                ),
            )
        )

        if deep:
            reconciliation = reconcile_schema(
                engine,
                db_schema=db_schema,
                vocabulary_included=vocabulary_included,
            )
            blocking_issue_count = sum(
                1 for issue in reconciliation.issues if is_blocking_issue(issue)
            )
            checks.append(
                DoctorCheck(
                    name="schema drift",
                    status=(
                        Status.PASSED
                        if not blocking_issue_count
                        else Status.WARNING
                    ),
                    detail=(
                        "ORM metadata matches the target database."
                        if not blocking_issue_count
                        else f"{blocking_issue_count} difference(s) detected."
                    ),
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    name="schema drift",
                    status=Status.SKIPPED,
                    detail="Run `omop-alchemy doctor --deep` to reconcile ORM metadata against the target database.",
                )
            )

        if info.backend == SupportedDialect.POSTGRESQL:
            foreign_key_status = tuple(
                collect_foreign_key_trigger_status(
                    engine,
                    db_schema=db_schema,
                    vocabulary_included=vocabulary_included,
                )
            )
            disabled_tables = sum(
                item.disabled_trigger_count > 0 for item in foreign_key_status
            )
            checks.append(
                DoctorCheck(
                    name="foreign keys",
                    status=(
                        Status.PASSED if disabled_tables == 0 else Status.WARNING
                    ),
                    detail=(
                        "All inspected RI triggers are enabled."
                        if disabled_tables == 0
                        else f"{disabled_tables} table(s) still have disabled RI triggers."
                    ),
                )
            )

            if deep:
                foreign_key_validation = validate_foreign_key_constraints(
                    engine,
                    db_schema=db_schema,
                    vocabulary_included=vocabulary_included,
                )
                violating_tables = sum(
                    result.status == Status.FAILED
                    for result in foreign_key_validation.results
                )
                checks.append(
                    DoctorCheck(
                        name="foreign key validation",
                        status=(
                            Status.PASSED
                            if violating_tables == 0
                            else Status.FAILED
                        ),
                        detail=(
                            "All selected foreign key relationships passed validation."
                            if violating_tables == 0
                            else f"{violating_tables} table(s) have violating foreign key rows."
                        ),
                    )
                )
            else:
                checks.append(
                    DoctorCheck(
                        name="foreign key validation",
                        status=Status.SKIPPED,
                        detail="Run `omop-alchemy doctor --deep` to validate selected foreign key relationships.",
                    )
                )
        else:
            checks.append(
                DoctorCheck(
                    name="foreign keys",
                    status=Status.SKIPPED,
                    detail="Foreign key trigger inspection is only available on PostgreSQL.",
                )
            )
            checks.append(
                DoctorCheck(
                    name="foreign key validation",
                    status=Status.SKIPPED,
                    detail="Foreign key validation is only available on PostgreSQL.",
                )
            )
    else:
        checks.extend(
            (
                DoctorCheck(
                    name="managed tables",
                    status=Status.SKIPPED,
                    detail="Skipped because the database connection is not ready.",
                ),
                DoctorCheck(
                    name="foreign keys",
                    status=Status.SKIPPED,
                    detail="Skipped because the database connection is not ready.",
                ),
                DoctorCheck(
                    name="schema drift",
                    status=Status.SKIPPED,
                    detail="Skipped because the database connection is not ready.",
                ),
                DoctorCheck(
                    name="foreign key validation",
                    status=Status.SKIPPED,
                    detail="Skipped because the database connection is not ready.",
                ),
            )
        )

    if info.backend == SupportedDialect.POSTGRESQL:
        backup_tools_ready = info.pg_dump_path is not None and (
            info.pg_restore_path is not None or info.psql_path is not None
        )
        checks.append(
            DoctorCheck(
                name="backup tooling",
                status=Status.PASSED if backup_tools_ready else Status.WARNING,
                detail=(
                    "PostgreSQL backup and restore client tools are available."
                    if backup_tools_ready
                    else "PostgreSQL client tools are incomplete on this machine."
                ),
            )
        )
    else:
        checks.append(
            DoctorCheck(
                name="backup tooling",
                status=Status.SKIPPED,
                detail="Backup and restore tooling checks are only relevant for PostgreSQL targets.",
            )
        )

    return DoctorReport(
        info=info,
        checks=tuple(checks),
        recommendations=_build_recommendations(
            info=info,
            reconciliation=reconciliation,
            foreign_key_status=foreign_key_status,
            foreign_key_validation=foreign_key_validation,
        ),
        reconciliation=reconciliation,
        foreign_key_status=foreign_key_status,
        foreign_key_validation=foreign_key_validation,
    )
