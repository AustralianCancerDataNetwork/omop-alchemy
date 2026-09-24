"""Table creation domain: detecting and creating ORM-managed OMOP tables that are absent from the target database."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import ExitStack
from dataclasses import dataclass

import sqlalchemy as sa

from oa_configurator import (
    ResolvedCDMDatabase,
    Role,
    ensure_schema,
    guard_schema_provenance_for,
    physical_schema_of,
    validate_schema_tag,
)
from orm_loader.helpers import Base
from ._cli_utils import Status, dry_label, dry_status
from .tables import (
    MaintenanceTable,
    TableCategory,
    missing_maintenance_tables,
)


def _distinct_schema_tags(tables: Iterable[sa.Table]) -> set[str]:
    """Every distinct schema tag among tables. Skips untagged tables."""
    return {tag for table in tables if (tag := validate_schema_tag(table)) is not None}


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
    vocabulary_included: bool = True,
) -> list[MaintenanceTable]:
    """Return ORM-managed tables that are absent from the target database, each checked against its own role's schema."""
    return missing_maintenance_tables(
        engine,
        vocabulary_included=vocabulary_included,
    )


def create_missing_tables(
    engine: sa.Engine,
    *,
    vocab_engine: sa.Engine | None = None,
    vocabulary_included: bool = True,
    dry_run: bool = False,
    resolved: ResolvedCDMDatabase | None = None,
) -> list[TableCreationResult]:
    """Create any ORM-managed tables missing from the target database. Skips tables with unresolved FK dependencies.

    Parameters
    ----------
    vocab_engine : sqlalchemy.Engine, optional
        Engine for vocab-role tables, when ``vocab_connection`` names a
        physically different server than ``engine``. Defaults to ``engine``
        (the common, same-connection case).
    resolved : ResolvedCDMDatabase, optional
        Enables the schema-provenance guard around each ``create_all()``
        call. Omitted by direct test/programmatic callers that hand in a
        bare engine with no resolved config behind it, in which case the
        guard no-ops. A role whose connection is test_only=true also
        no-ops, at the guard's own discretion.
    """
    vocab_engine = vocab_engine if vocab_engine is not None else engine
    if not dry_run:
        ensure_schema(engine, physical_schema_of(engine, schema_tag=Role.PRIMARY))
        # create_all() would fail for a non-existing schema on a fresh database.
        if resolved is not None:
            # Ensure schemas in split-engined deployments
            for schema_tag in _distinct_schema_tags(Base.metadata.tables.values()):
                # Primary schema is already ensure above
                if schema_tag == Role.PRIMARY.value:
                    continue
                target_engine = vocab_engine if schema_tag == Role.VOCAB.value else engine
                ensure_schema(target_engine, physical_schema_of(target_engine, schema_tag=schema_tag))
    inspector = sa.inspect(engine)
    missing_tables = collect_missing_tables(
        engine,
        vocabulary_included=vocabulary_included,
    )
    # Checking only primary schema would hide existing tables elsewhere, wrongly blocking dependents.
    existing_table_names: set[str] = set()
    for schema_tag in _distinct_schema_tags(Base.metadata.tables.values()):
        existing_table_names |= set(inspector.get_table_names(schema=physical_schema_of(engine, schema_tag=schema_tag)))
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
        vocab_tables = [table for table in all_tables if table.schema == Role.VOCAB.value]
        other_tables = [table for table in all_tables if table.schema != Role.VOCAB.value]
        if vocab_engine is engine:
            # One call: create_all's dependency sort and FK-deferral must see every table together.
            with engine.begin() as connection, ExitStack() as guards:
                for schema_tag in _distinct_schema_tags(all_tables):
                    tables_for_tag = [table for table in all_tables if table.schema == schema_tag]
                    guards.enter_context(
                        guard_schema_provenance_for(connection, resolved, schema_tag=schema_tag, tables=tables_for_tag)
                    )
                Base.metadata.create_all(
                    bind=connection, tables=all_tables, checkfirst=True
                )
        else:
            # Split physical connections: a cross-boundary FK can't be created here at all;
            # that failure surfaces from create_all itself rather than being masked.
            if other_tables:
                with engine.begin() as connection, ExitStack() as guards:
                    for schema_tag in _distinct_schema_tags(other_tables):
                        tables_for_tag = [table for table in other_tables if table.schema == schema_tag]
                        guards.enter_context(
                            guard_schema_provenance_for(connection, resolved, schema_tag=schema_tag, tables=tables_for_tag)
                        )
                    Base.metadata.create_all(
                        bind=connection, tables=other_tables, checkfirst=True
                    )
            if vocab_tables:
                with (
                    vocab_engine.begin() as vocab_connection,
                    guard_schema_provenance_for(vocab_connection, resolved, schema_tag=Role.VOCAB, tables=vocab_tables),
                ):
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
