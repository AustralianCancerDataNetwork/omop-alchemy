"""Execution helpers for one PostgreSQL materialized view at a time."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import sqlalchemy as sa

from .contracts import (
    MaterializedSelectable,
    MaterializedViewIndex,
    MaterializedViewTarget,
)
from .ddl import (
    CreateMaterializedView,
    CreateMaterializedViewIndex,
    DropMaterializedView,
    RefreshMaterializedView,
)


class MaterializationOperation(StrEnum):
    """Lifecycle operation recorded in outcomes and failures."""

    create = "create"
    create_index = "create_index"
    inspect_indexes = "inspect_indexes"
    refresh = "refresh"
    drop = "drop"


@dataclass(frozen=True, slots=True)
class MaterializationOutcome:
    """A successfully executed materialized-view operation."""

    operation: MaterializationOperation
    target: MaterializedViewTarget
    index_name: str | None = None


@dataclass(frozen=True, slots=True)
class MaterializationFailure:
    """Structured context for a failed materialized-view operation."""

    operation: MaterializationOperation
    target: MaterializedViewTarget
    reason: str
    index_name: str | None = None
    cause: BaseException | None = None


class MaterializationError(RuntimeError):
    """Base exception carrying structured materialization failure context."""

    def __init__(self, failure: MaterializationFailure) -> None:
        self.failure = failure
        super().__init__(
            f"Could not {failure.operation} materialized view "
            f"{failure.target.schema}.{failure.target.name}: {failure.reason}"
        )


class UnsupportedMaterializationDialectError(MaterializationError):
    """Raised before execution when a connection is not PostgreSQL."""


class ConcurrentRefreshNotEligibleError(MaterializationError):
    """Raised before refresh when no eligible unique index is available."""


_ELIGIBLE_UNIQUE_INDEX_SQL = sa.text(
    """
    SELECT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class AS materialized_view
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = materialized_view.relnamespace
        JOIN pg_catalog.pg_index AS index_definition
          ON index_definition.indrelid = materialized_view.oid
        JOIN pg_catalog.pg_class AS index_relation
          ON index_relation.oid = index_definition.indexrelid
        WHERE namespace.nspname = :schema
          AND materialized_view.relname = :name
          AND index_relation.relname IN :index_names
          AND materialized_view.relkind = 'm'
          AND index_definition.indisunique
          AND index_definition.indisvalid
          AND index_definition.indisready
          AND index_definition.indpred IS NULL
          AND index_definition.indexprs IS NULL
    )
    """
).bindparams(sa.bindparam("index_names", expanding=True))


def _require_postgresql(
    connection: sa.Connection,
    *,
    operation: MaterializationOperation,
    target: MaterializedViewTarget,
) -> None:
    if connection.dialect.name == "postgresql":
        return
    failure = MaterializationFailure(
        operation=operation,
        target=target,
        reason=(
            "materialized-view lifecycle operations require PostgreSQL; "
            f"received {connection.dialect.name!r}"
        ),
    )
    raise UnsupportedMaterializationDialectError(failure)


def _execute(
    connection: sa.Connection,
    statement: Any,
    *,
    operation: MaterializationOperation,
    target: MaterializedViewTarget,
    index_name: str | None = None,
) -> MaterializationOutcome:
    _require_postgresql(connection, operation=operation, target=target)
    try:
        connection.execute(statement)
    except Exception as error:
        failure = MaterializationFailure(
            operation=operation,
            target=target,
            index_name=index_name,
            reason=str(error),
            cause=error,
        )
        raise MaterializationError(failure) from error
    return MaterializationOutcome(
        operation=operation,
        target=target,
        index_name=index_name,
    )


def create_materialized_view(
    connection: sa.Connection,
    materialized: MaterializedSelectable,
    *,
    with_data: bool = True,
    if_not_exists: bool = False,
) -> MaterializationOutcome:
    """Create a materialized view at its declared qualified target."""
    return _execute(
        connection,
        CreateMaterializedView(
            materialized.target,
            materialized.selectable,
            with_data=with_data,
            if_not_exists=if_not_exists,
        ),
        operation=MaterializationOperation.create,
        target=materialized.target,
    )


def create_materialized_view_index(
    connection: sa.Connection,
    target: MaterializedViewTarget,
    index: MaterializedViewIndex,
    *,
    if_not_exists: bool = False,
) -> MaterializationOutcome:
    """Create one declared index against the same qualified view target."""
    return _execute(
        connection,
        CreateMaterializedViewIndex(
            target,
            index,
            if_not_exists=if_not_exists,
        ),
        operation=MaterializationOperation.create_index,
        target=target,
        index_name=index.name,
    )


def create_materialized_view_indexes(
    connection: sa.Connection,
    materialized: MaterializedSelectable,
    *,
    if_not_exists: bool = False,
) -> tuple[MaterializationOutcome, ...]:
    """Create every index declared by a materialized selectable."""
    return tuple(
        create_materialized_view_index(
            connection,
            materialized.target,
            index,
            if_not_exists=if_not_exists,
        )
        for index in materialized.indexes
    )


def materialized_view_has_eligible_unique_index(
    connection: sa.Connection,
    materialized: MaterializedSelectable,
) -> bool:
    """Confirm that a declared unique index is eligible in PostgreSQL."""
    operation = MaterializationOperation.inspect_indexes
    target = materialized.target
    _require_postgresql(connection, operation=operation, target=target)
    index_names = tuple(index.name for index in materialized.indexes if index.unique)
    if not index_names:
        return False
    try:
        return bool(
            connection.execute(
                _ELIGIBLE_UNIQUE_INDEX_SQL,
                {
                    "schema": target.schema,
                    "name": target.name,
                    "index_names": index_names,
                },
            ).scalar_one()
        )
    except Exception as error:
        failure = MaterializationFailure(
            operation=operation,
            target=target,
            reason=str(error),
            cause=error,
        )
        raise MaterializationError(failure) from error


def _require_concurrent_refresh_eligibility(
    connection: sa.Connection,
    materialized: MaterializedSelectable,
) -> None:
    if not any(index.unique for index in materialized.indexes):
        raise ConcurrentRefreshNotEligibleError(
            MaterializationFailure(
                operation=MaterializationOperation.refresh,
                target=materialized.target,
                reason="no simple unique index is declared",
            )
        )
    if not materialized_view_has_eligible_unique_index(
        connection,
        materialized,
    ):
        raise ConcurrentRefreshNotEligibleError(
            MaterializationFailure(
                operation=MaterializationOperation.refresh,
                target=materialized.target,
                reason="PostgreSQL has no eligible unique index on the target",
            )
        )


def refresh_materialized_view(
    connection: sa.Connection,
    materialized: MaterializedSelectable,
    *,
    concurrently: bool = False,
) -> MaterializationOutcome:
    """Refresh a materialized view after any concurrent-refresh preflight."""
    if concurrently:
        # Concurrent refresh performs catalog inspection before execution, so it
        # needs the dialect guard before preflight. Ordinary refresh reaches the
        # same guard once through _execute().
        _require_postgresql(
            connection,
            operation=MaterializationOperation.refresh,
            target=materialized.target,
        )
        _require_concurrent_refresh_eligibility(connection, materialized)
    return _execute(
        connection,
        RefreshMaterializedView(
            materialized.target,
            concurrently=concurrently,
        ),
        operation=MaterializationOperation.refresh,
        target=materialized.target,
    )


def drop_materialized_view(
    connection: sa.Connection,
    materialized: MaterializedSelectable,
    *,
    if_exists: bool = True,
    cascade: bool = False,
) -> MaterializationOutcome:
    """Drop one materialized view, preserving the original database failure."""
    return _execute(
        connection,
        DropMaterializedView(
            materialized.target,
            if_exists=if_exists,
            cascade=cascade,
        ),
        operation=MaterializationOperation.drop,
        target=materialized.target,
    )
