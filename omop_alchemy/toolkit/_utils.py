"""Shared implementation details for toolkit query builders."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from typing import Any

import sqlalchemy as sa

from sqlalchemy.sql.selectable import FromClause, SelectBase


def _nullable_column(
    model: type[Any],
    name: str,
    sql_type: sa.types.TypeEngine[Any],
) -> sa.ColumnElement[Any]:
    """Project a labelled column or a typed NULL when the model lacks it."""
    column = getattr(model, str(name), None)
    if column is None:
        # Unions need the same column positions across heterogeneous sources.
        return sa.cast(sa.null(), sql_type).label(str(name))
    return column.label(str(name))


def _select_or_union_all(
    projections: Sequence[sa.Select[Any]],
    *,
    error_message: str,
) -> sa.Select[Any] | sa.CompoundSelect[Any]:
    """Return one projection directly or combine several with ``UNION ALL``."""
    if not projections:
        raise ValueError(error_message)
    return projections[0] if len(projections) == 1 else sa.union_all(*projections)


def _as_from_clause(
    source: FromClause | SelectBase,
    *,
    name: str,
) -> FromClause:
    """Coerce a selectable to a named ``FromClause`` for query composition."""
    if isinstance(source, SelectBase):
        return source.subquery(name)
    if isinstance(source, FromClause):
        return source
    raise TypeError(f"{name} must be a SQLAlchemy Select or FromClause")


def _require_columns(
    available: Collection[str],
    required: Iterable[str],
    *,
    role: str,
    error_type: type[Exception],
) -> None:
    """Raise a domain-specific error when required column names are absent."""
    missing = tuple(sorted(set(required) - set(available)))
    if missing:
        raise error_type(f"{role} is missing required columns: {', '.join(missing)}")
