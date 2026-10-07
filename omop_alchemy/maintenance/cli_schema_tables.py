"""Table creation domain: detecting and creating ORM-managed OMOP tables that are absent from the target database."""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa

from oa_configurator import (
    ResolvedCDMDatabase,
    UnregisteredSchemaTagError,
    claimed_schema_tags,
    declared_schema_tags,
    physical_schema_of,
)
from orm_loader.helpers import Base
from ._cli_utils import Status, dry_label, dry_status
from .tables import (
    MaintenanceTable,
    TableCategory,
    missing_maintenance_tables,
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
            "aren't claimed on this connection. Add them to create_cdm_engine()'s own "
            "create_engine(schema_claims=[...]) call."
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
    engine: sa.Engine,
    *,
    vocab_engine: sa.Engine,
    vocabulary_included: bool = True,
    resolved: ResolvedCDMDatabase,
) -> list[MaintenanceTable]:
    """Return ORM-managed tables that are absent from the target database, each checked against its own role's schema."""
    return missing_maintenance_tables(
        engine,
        vocab_bindable=vocab_engine,
        vocabulary_included=vocabulary_included,
        resolved=resolved,
    )


def _create_missing_tables(
    engine: sa.Engine,
    *,
    vocab_engine: sa.Engine,
    vocabulary_included: bool = True,
    dry_run: bool = False,
    resolved: ResolvedCDMDatabase,
) -> list[TableCreationResult]:
    """Create any ORM-managed tables missing from the target database. Skips tables with unresolved FK dependencies.

    Parameters
    ----------
    vocab_engine : sqlalchemy.Engine
        Engine for vocab-role tables, when ``vocab_connection`` names a
        physically different server than ``engine``.
    resolved : ResolvedCDMDatabase
        Enables the schema-provenance guard around each ``create_all()``
        call, and ensures every non-primary role's schema exists in a
        split-engined deployment. A role whose connection is
        test_only=true no-ops the guard, at the guard's own discretion.

    Notes
    -----
    - No provenance guard as engine was just built in `omop_command`. There is no possibility 
    of schema drift between the engine's creation and this command's execution.
    """
    missing_tables = collect_missing_tables(
        engine,
        vocab_engine=vocab_engine,
        vocabulary_included=vocabulary_included,
        resolved=resolved,
    )
    # Checking only primary schema would hide existing tables elsewhere, wrongly blocking dependents.
    existing_table_names: set[str] = set()
    for schema_tag in declared_schema_tags(Base.metadata.tables.values()):
        target_engine = resolved.route_for_schema_tag(schema_tag, vocab=vocab_engine, primary=engine)
        existing_table_names |= set(
            sa.inspect(target_engine).get_table_names(schema=physical_schema_of(target_engine, schema_tag=schema_tag))
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

    creatable_tables = [
        table
        for table in missing_tables
        if table.table_name not in blocked_dependencies
    ]

    results: list[TableCreationResult] = []
    if creatable_tables and not dry_run:
        all_tables = [table.table for table in creatable_tables]
        vocab_tables = [
            table for table in all_tables
            # SQLAlchemy's own stub omits None from schema's declared type,
            # despite accepting and correctly handling it at runtime.
            if resolved.route_for_schema_tag(table.schema, vocab=vocab_engine, primary=engine) is vocab_engine  # ty: ignore[invalid-argument-type]
        ]
        other_tables = [
            table for table in all_tables
            if resolved.route_for_schema_tag(table.schema, vocab=vocab_engine, primary=engine) is not vocab_engine  # ty: ignore[invalid-argument-type]
        ]

        if vocab_engine is engine:
            # One call: create_all's dependency sort and FK-deferral must see every table together.
            with engine.begin() as connection:
                _assert_tags_claimed(connection, all_tables, context="_create_missing_tables()")
                Base.metadata.create_all(
                    bind=connection, tables=all_tables, checkfirst=True
                )
        else:
            # Split physical connections: a cross-boundary FK can't be created here at all;
            # that failure surfaces from create_all itself rather than being masked.
            if other_tables:
                with engine.begin() as connection:
                    _assert_tags_claimed(
                        connection, other_tables, context="_create_missing_tables() (primary connection)"
                    )
                    Base.metadata.create_all(
                        bind=connection, tables=other_tables, checkfirst=True
                    )
            if vocab_tables:
                with vocab_engine.begin() as vocab_connection:
                    _assert_tags_claimed(
                        vocab_connection, vocab_tables, context="_create_missing_tables() (vocab connection)"
                    )
                    Base.metadata.create_all(
                        bind=vocab_connection, tables=vocab_tables, checkfirst=True
                    )

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
