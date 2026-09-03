"""Rectify domain: acknowledge a deliberate schema change, or clean up orphaned
tables left behind by one.

Every action requires an explicit target and confirmation; nothing here
auto-detects or auto-deletes.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from oa_configurator import ResolvedCDMDatabase, Resolver, StackConfig, schema_inspect

from ..backends import backend_supports, resolve_backend


@dataclass(frozen=True)
class OrphanTablePreview:
    """One table found in a candidate orphan schema, with an approximate row count."""

    table_name: str
    approximate_row_count: int | None


def preview_orphan_schema_tables(connection: sa.Connection, schema: str) -> list[OrphanTablePreview]:
    """List tables physically present in schema, with an approximate row count
    where the backend supports one (None otherwise).
    """
    table_names = schema_inspect(connection, schema=schema).get_table_names()
    backend = resolve_backend(connection.engine)
    counts: dict[str, int] = (
        backend.approximate_row_counts(connection, schema)
        if backend_supports(backend, "approximate_row_counts")
        else {}
    )
    return [
        OrphanTablePreview(table_name=name, approximate_row_count=counts.get(name))
        for name in table_names
    ]


def schema_is_a_current_target(stack: StackConfig, schema: str) -> str | None:
    """Return the name of a configured database whose current schema (or
    vocab/results schema) equals schema, or None.

    Checks every database in the stack, not just the one named on the
    command line, since two CDM databases can legitimately share one
    vocabulary schema; dropping tables there would destroy a database a
    different entry is actively using.
    """
    resolver = Resolver(stack)
    for name in stack.databases:
        try:
            resolved = resolver.resolve_database(name)
        except Exception:
            continue
        candidates: set[str | None] = {resolved.schema_name}
        if isinstance(resolved, ResolvedCDMDatabase):
            candidates.add(resolved.vocab_schema)
            candidates.add(resolved.results_schema)
        if schema in candidates:
            return name
    return None


def drop_orphan_schema_tables(
    connection: sa.Connection,
    *,
    stack: StackConfig,
    orphan_schema: str,
    confirm: bool,
) -> list[OrphanTablePreview]:
    """Drop every table found in *orphan_schema*, after the cross-entry safety check.

    Preview-only when *confirm* is False: returns what would be dropped
    without touching anything.

    Raises
    ------
    RuntimeError
        If *orphan_schema* is the current schema target of any configured
        database/role in *stack*.
    """
    blocking = schema_is_a_current_target(stack, orphan_schema)
    if blocking is not None:
        raise RuntimeError(
            f"Refusing to drop tables in schema {orphan_schema!r}: it is the current schema "
            f"target of database {blocking!r}. Reconfigure or drop that database entry first "
            "if this schema is genuinely meant to be retired."
        )
    preview = preview_orphan_schema_tables(connection, orphan_schema)
    if confirm:
        for item in preview:
            connection.execute(
                sa.text(f'DROP TABLE IF EXISTS "{orphan_schema}"."{item.table_name}" CASCADE')
            )
    return preview
