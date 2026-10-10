from __future__ import annotations

import sqlalchemy as sa
from oa_configurator import Dialect, Role

from .base import Backend, FeatureNotSupportedError


class SQLiteBackend(Backend):

    @property
    def name(self) -> str:
        return "SQLite"

    @property
    def dialect(self) -> str:
        return Dialect.SQLITE

    def index_exists(
        self,
        conn: sa.Connection,
        index_name: str,
        *,
        schema_tag: str = Role.PRIMARY.value,
    ) -> bool:
        row = conn.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
            (index_name,),
        ).fetchone()
        return row is not None

    def analyze_table(
        self,
        conn: sa.Connection,
        table_name: str,
        *,
        vacuum: bool = False,
        schema_tag: str = Role.PRIMARY.value,
    ) -> None:
        if vacuum:
            raise FeatureNotSupportedError("VACUUM ANALYZE", self)
        conn.exec_driver_sql(f'ANALYZE "{table_name}"')
