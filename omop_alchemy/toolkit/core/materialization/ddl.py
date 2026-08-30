"""PostgreSQL DDL for schema-qualified materialized views."""

from __future__ import annotations

from typing import Any

from sqlalchemy.engine import Dialect
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import DDLElement
from sqlalchemy.sql.compiler import DDLCompiler, IdentifierPreparer
from sqlalchemy.sql.selectable import SelectBase

from .contracts import MaterializedViewIndex, MaterializedViewTarget


def _quote_identifier(preparer: IdentifierPreparer, value: str) -> str:
    return preparer.quote_identifier(value)


def _qualified_target(
    preparer: IdentifierPreparer,
    target: MaterializedViewTarget,
) -> str:
    return ".".join(
        (
            _quote_identifier(preparer, target.schema),
            _quote_identifier(preparer, target.name),
        )
    )


def render_materialized_view_target(
    target: MaterializedViewTarget,
    dialect: Dialect,
) -> str:
    """Render a fully quoted materialized-view identifier for ``dialect``."""
    return _qualified_target(dialect.identifier_preparer, target)


class CreateMaterializedView(DDLElement):
    """Create one PostgreSQL materialized view from a selectable."""

    inherit_cache = False

    def __init__(
        self,
        target: MaterializedViewTarget,
        selectable: SelectBase,
        *,
        with_data: bool = True,
        if_not_exists: bool = False,
    ) -> None:
        self.view_target = target
        self.selectable = selectable
        self.with_data = with_data
        self.if_not_exists = if_not_exists


class RefreshMaterializedView(DDLElement):
    """Refresh one PostgreSQL materialized view."""

    inherit_cache = False

    def __init__(
        self,
        target: MaterializedViewTarget,
        *,
        concurrently: bool = False,
    ) -> None:
        self.view_target = target
        self.concurrently = concurrently


class DropMaterializedView(DDLElement):
    """Drop one PostgreSQL materialized view."""

    inherit_cache = False

    def __init__(
        self,
        target: MaterializedViewTarget,
        *,
        if_exists: bool = True,
        cascade: bool = False,
    ) -> None:
        self.view_target = target
        self.if_exists = if_exists
        self.cascade = cascade


class CreateMaterializedViewIndex(DDLElement):
    """Create one declared simple index on a materialized view."""

    inherit_cache = False

    def __init__(
        self,
        target: MaterializedViewTarget,
        index: MaterializedViewIndex,
        *,
        if_not_exists: bool = False,
    ) -> None:
        self.view_target = target
        self.index = index
        self.if_not_exists = if_not_exists


@compiles(CreateMaterializedView, "postgresql")
def _compile_create_materialized_view(
    element: CreateMaterializedView,
    compiler: DDLCompiler,
    **_: Any,
) -> str:
    target = _qualified_target(compiler.preparer, element.view_target)
    selectable = compiler.sql_compiler.process(
        element.selectable,
        literal_binds=True,
    )
    existence = " IF NOT EXISTS" if element.if_not_exists else ""
    population = "WITH DATA" if element.with_data else "WITH NO DATA"
    return f"CREATE MATERIALIZED VIEW{existence} {target} AS {selectable} {population}"


@compiles(RefreshMaterializedView, "postgresql")
def _compile_refresh_materialized_view(
    element: RefreshMaterializedView,
    compiler: DDLCompiler,
    **_: Any,
) -> str:
    target = _qualified_target(compiler.preparer, element.view_target)
    concurrency = " CONCURRENTLY" if element.concurrently else ""
    return f"REFRESH MATERIALIZED VIEW{concurrency} {target}"


@compiles(DropMaterializedView, "postgresql")
def _compile_drop_materialized_view(
    element: DropMaterializedView,
    compiler: DDLCompiler,
    **_: Any,
) -> str:
    target = _qualified_target(compiler.preparer, element.view_target)
    existence = " IF EXISTS" if element.if_exists else ""
    cascade = " CASCADE" if element.cascade else ""
    return f"DROP MATERIALIZED VIEW{existence} {target}{cascade}"


@compiles(CreateMaterializedViewIndex, "postgresql")
def _compile_create_materialized_view_index(
    element: CreateMaterializedViewIndex,
    compiler: DDLCompiler,
    **_: Any,
) -> str:
    target = _qualified_target(compiler.preparer, element.view_target)
    index_name = _quote_identifier(compiler.preparer, element.index.name)
    columns = ", ".join(
        _quote_identifier(compiler.preparer, column) for column in element.index.columns
    )
    uniqueness = "UNIQUE " if element.index.unique else ""
    existence = " IF NOT EXISTS" if element.if_not_exists else ""
    return f"CREATE {uniqueness}INDEX{existence} {index_name} ON {target} ({columns})"
