"""The resolved CDM database and engine pair every maintenance command works against."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import sqlalchemy as sa
from oa_configurator import ResolvedCDMDatabase, physical_schema_of, referred_schema_tag

from ..cross_database import check_engine_pair
from .tables import MaintenanceTable, TableTarget


@dataclass(frozen=True, kw_only=True)
class MaintenanceContext:
    """Resolved CDM database plus its ``(engine, vocab_engine)`` pair.

    The one place maintenance work is routed: :meth:`targets` resolves each
    table's engine, physical schema and foreign-key feasibility, so helpers
    never map the routing rule themselves.

    Attributes
    ----------
    resolved : ResolvedCDMDatabase
    engine : sqlalchemy.Engine
        Primary engine, from ``create_engines()``.
    vocab_engine : sqlalchemy.Engine
        Vocabulary engine from the same call, ``engine`` itself when the
        vocabulary shares its database.
    resource_name : str
        Database entry name, for display.
    athena_source : str or None
        Configured Athena vocabulary directory.

    Raises
    ------
    ValueError
        If the engines do not address *resolved*'s own role connections.
    """

    resolved: ResolvedCDMDatabase
    engine: sa.Engine
    vocab_engine: sa.Engine
    resource_name: str = ""
    athena_source: str | None = None

    def __post_init__(self) -> None:
        check_engine_pair(self.resolved, primary=self.engine, vocab=self.vocab_engine)

    @property
    def engines(self) -> tuple[sa.Engine, ...]:
        """Each distinct engine once: one when colocated, two when split."""
        return tuple(dict.fromkeys((self.engine, self.vocab_engine)))

    def engine_for(self, schema_tag: str) -> sa.Engine:
        """Engine hosting *schema_tag*."""
        return self.resolved.route_for_schema_tag(
            schema_tag, vocab=self.vocab_engine, primary=self.engine
        )

    def targets(self, tables: Iterable[MaintenanceTable]) -> list[TableTarget]:
        """Engine, physical schema and foreign-key feasibility for each table."""
        targets = []
        for table in tables:
            bind = self.engine_for(table.schema_tag)
            targets.append(
                TableTarget(
                    table=table,
                    bind=bind,
                    physical_schema=physical_schema_of(bind, schema_tag=table.schema_tag),
                    foreign_keys_creatable=self._foreign_keys_creatable(table),
                )
            )
        return targets

    def targets_by_engine(
        self, tables: Iterable[MaintenanceTable]
    ) -> dict[sa.Engine, list[TableTarget]]:
        """:meth:`targets` grouped by engine, in first-seen order."""
        groups: dict[sa.Engine, list[TableTarget]] = {}
        for target in self.targets(tables):
            groups.setdefault(target.bind, []).append(target)
        return groups

    def _foreign_keys_creatable(self, table: MaintenanceTable) -> bool:
        """Can every foreign key declared on *table* physically exist?"""
        own_tag = table.schema_tag
        return all(
            self.resolved.foreign_key_can_span(own_tag, referred_schema_tag(element, own_tag))
            for constraint in table.table.foreign_key_constraints
            for element in constraint.elements
        )
