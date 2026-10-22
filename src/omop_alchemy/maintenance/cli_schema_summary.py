"""Data summary domain: collecting row counts and existence state for ORM-managed OMOP tables."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass

import sqlalchemy as sa

from oa_configurator import qualified
from .context import MaintenanceContext
from .tables import TableCategory, select_omop_tables


@dataclass(frozen=True)
class TableSummaryResult:
    """Row count and existence data for one ORM-managed OMOP table."""

    table_name: str
    category: TableCategory
    schema_tag: str
    model_name: str
    primary_key_columns: tuple[str, ...]
    exists: bool
    row_count: int | None


def collect_data_summary(
    context: MaintenanceContext,
    *,
    vocabulary_included: bool = False,
    existing_only: bool = True,
) -> list[TableSummaryResult]:
    """Return row counts and existence state for each ORM-managed table, each read on its own engine."""
    tables = select_omop_tables(vocabulary_included=vocabulary_included)

    results: list[TableSummaryResult] = []
    with ExitStack() as stack:
        connections: dict[sa.Engine, sa.Connection] = {}
        for target in context.targets(tables):
            table = target.table
            exists = target.exists()
            if not exists and existing_only:
                continue

            row_count: int | None = None
            if exists:
                if target.bind not in connections:
                    connections[target.bind] = stack.enter_context(target.bind.connect())
                connection = connections[target.bind]
                row_count = int(
                    connection.execute(
                        sa.text(
                            f"SELECT COUNT(*) FROM {qualified(connection, table.table_name, physical_schema=target.physical_schema)}"
                        )
                    ).scalar_one()
                )

            results.append(
                TableSummaryResult(
                    table_name=table.table_name,
                    category=table.category,
                    schema_tag=table.schema_tag,
                    model_name=table.model_name,
                    primary_key_columns=table.primary_key_names,
                    exists=exists,
                    row_count=row_count,
                )
            )

    return results
