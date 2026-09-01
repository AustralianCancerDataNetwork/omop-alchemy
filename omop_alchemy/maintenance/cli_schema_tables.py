"""Table creation domain: detecting and creating ORM-managed OMOP tables that are absent from the target database."""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa

from oa_configurator import Role, ensure_schema
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
    db_schema: str | None = None,
    vocabulary_included: bool = True,
) -> list[MaintenanceTable]:
    """Return ORM-managed tables that are absent from the target database."""
    inspector = sa.inspect(engine)
    return missing_maintenance_tables(
        inspector,
        db_schema=db_schema,
        vocabulary_included=vocabulary_included,
    )


def create_missing_tables(
    engine: sa.Engine,
    *,
    vocab_engine: sa.Engine | None = None,
    db_schema: str | None = None,
    vocabulary_included: bool = True,
    dry_run: bool = False,
) -> list[TableCreationResult]:
    """Create any ORM-managed tables missing from the target database. Skips tables with unresolved FK dependencies.

    Parameters
    ----------
    vocab_engine : sqlalchemy.Engine, optional
        Engine for vocab-role tables, when ``vocab_connection`` names a
        physically different server than ``engine``. Defaults to ``engine``
        (the common, same-connection case).
    """
    vocab_engine = vocab_engine if vocab_engine is not None else engine
    if not dry_run:
        ensure_schema(engine, db_schema)
    inspector = sa.inspect(engine)
    missing_tables = collect_missing_tables(
        engine,
        db_schema=db_schema,
        vocabulary_included=vocabulary_included,
    )
    existing_table_names = set(inspector.get_table_names(schema=db_schema))
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
        if vocab_engine is engine:
            # One call: create_all's dependency sort and FK-deferral must see every table together.
            with engine.begin() as connection:
                Base.metadata.create_all(
                    bind=connection, tables=all_tables, checkfirst=True
                )
        else:
            # Split physical connections: a cross-boundary FK can't be created here at all;
            # that failure surfaces from create_all itself rather than being masked.
            vocab_tables = [
                table for table in all_tables if table.schema == Role.VOCAB.value
            ]
            other_tables = [
                table for table in all_tables if table.schema != Role.VOCAB.value
            ]
            if other_tables:
                with engine.begin() as connection:
                    Base.metadata.create_all(
                        bind=connection, tables=other_tables, checkfirst=True
                    )
            if vocab_tables:
                with vocab_engine.begin() as vocab_connection:
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
