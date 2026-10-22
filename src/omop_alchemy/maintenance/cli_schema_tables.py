"""Table creation domain: detecting and creating ORM-managed OMOP tables that are absent from the target database."""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa

from oa_configurator import (
    UnregisteredSchemaTagError,
    claimed_schema_tags,
    declared_schema_tags,
    physical_schema_of,
)
from orm_loader.helpers import Base, create_tables
from ._cli_utils import Status, dry_label, dry_status
from .context import MaintenanceContext
from .tables import (
    all_table_names,
    MaintenanceTable,
    TableCategory,
    missing_maintenance_targets,
)


@dataclass(frozen=True)
class TableCreationResult:
    """Outcome of attempting to create one missing ORM-managed table from SQLAlchemy metadata."""

    table_name: str
    category: TableCategory
    model_name: str
    status: Status
    detail: str


def _assert_tags_claimed(connection: sa.Connection, tables: list[sa.Table], *, context: str) -> None:
    """Raise a clear error if any of tables' declared schema tags isn't
    claimed on connection, instead of letting create_all() fail obscurely
    against a schema that was never created.

    context names the caller-specific situation (e.g. which connection's
    tables are being checked), so the error names the actual call site
    rather than hardcoding one caller's identity into a shared helper.
    """
    missing = declared_schema_tags(tables) - claimed_schema_tags(connection)
    if missing:
        raise UnregisteredSchemaTagError(
            f"{context}: table(s) declare schema tag(s) {sorted(missing)} that "
            "aren't claimed on this connection. Add them to create_cdm_engines()'s own "
            "create_engines(schema_claims=[...]) call."
        )


def _table_dependencies(table: MaintenanceTable) -> tuple[str, ...]:
    """Return the sorted names of tables that this table's ORM FK constraints refer to."""
    return tuple(
        sorted(
            {
                constraint.referred_table.name
                for constraint in table.table.foreign_key_constraints
            }
        )
    )


def collect_missing_tables(
    context: MaintenanceContext,
    *,
    vocabulary_included: bool = True,
) -> list[MaintenanceTable]:
    """Return ORM-managed tables that are absent, each checked on its own engine and schema."""
    return [
        target.table
        for target in missing_maintenance_targets(context, vocabulary_included=vocabulary_included)
    ]


def _create_missing_tables(
    context: MaintenanceContext,
    *,
    vocabulary_included: bool = True,
    dry_run: bool = False,
) -> list[TableCreationResult]:
    """Create any ORM-managed tables missing from their own engine. Skips tables with unresolved FK dependencies.

    Each engine creates its own tables through ``orm_loader``'s
    ``create_tables``, which leaves out foreign keys to a tag hosted on the
    other database, since no dialect can express those.

    Notes
    -----
    - No provenance guard as the engines were just built in `omop_command`. There is no
    possibility of schema drift between their creation and this command's execution.
    """
    missing_targets = missing_maintenance_targets(context, vocabulary_included=vocabulary_included)
    missing_tables = [target.table for target in missing_targets]
    # Checking only primary schema would hide existing tables elsewhere, wrongly blocking dependents.
    existing_table_names: set[str] = set()
    for schema_tag in declared_schema_tags(Base.metadata.tables.values()):
        target_engine = context.engine_for(schema_tag)
        existing_table_names |= all_table_names(
            target_engine,
            schema=physical_schema_of(target_engine, schema_tag=schema_tag),
        )
    missing_table_names = {table.table_name for table in missing_tables}

    blocked_dependencies: dict[str, tuple[str, ...]] = {}
    for maintenance_table in missing_tables:
        unresolved_dependencies = tuple(
            dependency_name
            for dependency_name in _table_dependencies(maintenance_table)
            if dependency_name not in existing_table_names
            and dependency_name not in missing_table_names
        )
        if unresolved_dependencies:
            blocked_dependencies[maintenance_table.table_name] = unresolved_dependencies

    if not dry_run:
        creatable: dict[sa.Engine, list[sa.Table]] = {}
        for target in missing_targets:
            if target.table_name not in blocked_dependencies:
                creatable.setdefault(target.bind, []).append(target.table.table)
        for engine, tables in creatable.items():
            # One call per engine: create_all's dependency sort and FK-deferral must see every table together.
            with engine.begin() as connection:
                _assert_tags_claimed(connection, tables, context="_create_missing_tables()")
                create_tables(connection, tables, resolved=context.resolved)

    results: list[TableCreationResult] = []
    for maintenance_table in missing_tables:
        blocked = blocked_dependencies.get(maintenance_table.table_name)
        results.append(
            TableCreationResult(
                table_name=maintenance_table.table_name,
                category=maintenance_table.category,
                model_name=maintenance_table.model_name,
                status=(
                    Status.BLOCKED
                    if blocked is not None
                    else dry_status(dry_run, applied=Status.CREATED)
                ),
                detail=(
                    "table blocked by unresolved dependencies: " + ", ".join(blocked)
                    if blocked is not None
                    else dry_label(dry_run, "table would be created from ORM metadata", "table created from ORM metadata")
                ),
            )
        )

    return results
