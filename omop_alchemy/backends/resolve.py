from __future__ import annotations

import sqlalchemy as sa
from oa_configurator import Dialect

from .base import Backend, BackendNotSupportedError
from .duckdb import DuckDBBackend
from .postgres import PostgresBackend
from .sqlite import SQLiteBackend

_DIALECT_TO_BACKEND_MAP: dict[Dialect, Backend] = {
    Dialect.POSTGRESQL: PostgresBackend(),
    Dialect.SQLITE: SQLiteBackend(),
    Dialect.DUCKDB: DuckDBBackend(),
}

def resolve_backend(engine: sa.Engine | sa.Connection) -> Backend:
    dialect = engine.dialect.name
    try:
        return _DIALECT_TO_BACKEND_MAP[Dialect(dialect)]
    except (ValueError, KeyError):
        raise BackendNotSupportedError(
            f"Unsupported database dialect: '{dialect}'. "
            f"Supported dialects: {', '.join(sorted(d.value for d in _DIALECT_TO_BACKEND_MAP))}."
        )


def backend_label(dialect_name: str) -> str:
    """Human-readable backend name for a dialect, falling back to the raw
    dialect name for anything unrecognized (e.g. connection not yet resolved)."""
    try:
        return _DIALECT_TO_BACKEND_MAP[Dialect(dialect_name)].name
    except (ValueError, KeyError):
        return dialect_name
