"""Materialized-view contracts, DDL, and lifecycle preflight tests."""

from __future__ import annotations

from typing import Any, cast

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.sql.elements import TextClause

from omop_alchemy.toolkit.core.materialization import (
    ConcurrentRefreshNotEligibleError,
    CreateMaterializedView,
    CreateMaterializedViewIndex,
    DropMaterializedView,
    MaterializationError,
    MaterializationOperation,
    MaterializedSelectable,
    MaterializedViewIndex,
    MaterializedViewSpec,
    MaterializedViewTarget,
    RefreshMaterializedView,
    UnsupportedMaterializationDialectError,
    drop_materialized_view,
    refresh_materialized_view,
    render_materialized_view_target,
)


def _spec(*, unique_index: bool = True) -> MaterializedViewSpec:
    indexes = (
        MaterializedViewIndex(
            name="person events identity",
            columns=("person_id", "event_id"),
            unique=unique_index,
        ),
    )
    return MaterializedViewSpec(
        target=MaterializedViewTarget(
            schema="analysis space",
            name='select"events',
        ),
        selectable=sa.select(
            sa.literal(1).label("person_id"),
            sa.literal(7).label("event_id"),
        ),
        logical_identity=("person_id", "event_id"),
        indexes=indexes,
    )


def test_materialized_view_spec_implements_public_protocol():
    assert isinstance(_spec(), MaterializedSelectable)


def test_materialized_view_spec_validates_identity_and_index_columns():
    target = MaterializedViewTarget(schema="reporting", name="events")
    selectable = sa.select(sa.literal(1).label("event_id"))

    with pytest.raises(ValueError, match="logical_identity columns"):
        MaterializedViewSpec(
            target=target,
            selectable=selectable,
            logical_identity=("person_id",),
        )
    with pytest.raises(ValueError, match="index .* columns"):
        MaterializedViewSpec(
            target=target,
            selectable=selectable,
            logical_identity=("event_id",),
            indexes=(
                MaterializedViewIndex(
                    name="bad_index",
                    columns=("missing_column",),
                ),
            ),
        )


def test_qualified_target_is_always_schema_qualified_and_quoted():
    rendered = render_materialized_view_target(
        _spec().target,
        postgresql.dialect(),
    )

    assert rendered == '"analysis space"."select""events"'


def test_all_ddl_uses_the_same_qualified_target():
    spec = _spec()
    dialect = postgresql.dialect()
    qualified = render_materialized_view_target(spec.target, dialect)
    statements = (
        CreateMaterializedView(spec.target, spec.selectable),
        CreateMaterializedViewIndex(spec.target, spec.indexes[0]),
        RefreshMaterializedView(spec.target, concurrently=True),
        DropMaterializedView(spec.target, cascade=True),
    )

    compiled = tuple(
        str(statement.compile(dialect=dialect)) for statement in statements
    )

    assert all(qualified in sql for sql in compiled)
    assert compiled[0].startswith("CREATE MATERIALIZED VIEW ")
    assert "IF NOT EXISTS" not in compiled[0]
    assert "CREATE UNIQUE INDEX " in compiled[1]
    assert "IF NOT EXISTS" not in compiled[1]
    assert '"person_id", "event_id"' in compiled[1]
    assert compiled[2].startswith("REFRESH MATERIALIZED VIEW CONCURRENTLY")
    assert compiled[3].endswith(" CASCADE")


class _ScalarResult:
    def __init__(self, value: bool) -> None:
        self.value = value

    def scalar_one(self) -> bool:
        return self.value


class _RecordingConnection:
    def __init__(
        self,
        *,
        dialect: sa.engine.Dialect | None = None,
        eligible_index: bool = False,
        error: Exception | None = None,
    ) -> None:
        self.dialect = dialect or postgresql.dialect()
        self.eligible_index = eligible_index
        self.error = error
        self.statements: list[Any] = []

    def execute(self, statement: Any, *_: Any, **__: Any) -> _ScalarResult:
        self.statements.append(statement)
        if self.error is not None:
            raise self.error
        return _ScalarResult(self.eligible_index)


def test_concurrent_refresh_without_declared_unique_index_executes_nothing():
    connection = _RecordingConnection()

    with pytest.raises(ConcurrentRefreshNotEligibleError) as error:
        refresh_materialized_view(
            cast(sa.Connection, connection),
            _spec(unique_index=False),
            concurrently=True,
        )

    assert connection.statements == []
    assert error.value.failure.operation is MaterializationOperation.refresh
    assert "no simple unique index" in error.value.failure.reason


def test_concurrent_refresh_requires_the_declared_index_to_exist_in_postgres():
    connection = _RecordingConnection(eligible_index=False)

    with pytest.raises(ConcurrentRefreshNotEligibleError) as error:
        refresh_materialized_view(
            cast(sa.Connection, connection),
            _spec(),
            concurrently=True,
        )

    assert len(connection.statements) == 1
    assert isinstance(connection.statements[0], TextClause)
    assert "PostgreSQL has no eligible unique index" in error.value.failure.reason


def test_drop_failure_retains_the_original_exception():
    original = RuntimeError("database transaction is aborted")
    connection = _RecordingConnection(error=original)

    with pytest.raises(MaterializationError) as error:
        drop_materialized_view(
            cast(sa.Connection, connection),
            _spec(),
        )

    assert error.value.failure.operation is MaterializationOperation.drop
    assert error.value.failure.cause is original
    assert error.value.__cause__ is original
    assert len(connection.statements) == 1


def test_lifecycle_rejects_non_postgresql_connections_before_execution():
    connection = _RecordingConnection(dialect=sqlite.dialect())

    with pytest.raises(UnsupportedMaterializationDialectError):
        drop_materialized_view(
            cast(sa.Connection, connection),
            _spec(),
        )

    assert connection.statements == []
