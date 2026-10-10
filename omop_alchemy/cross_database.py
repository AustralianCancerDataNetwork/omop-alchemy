"""Reading across a split primary/vocabulary deployment.

:func:`cdm_sessionmaker` builds sessions that send each statement to the
engine hosting its tables, so traversing a reference from a clinical row to
its concept works unchanged on a split deployment. :func:`filter_by_keys`
covers the opposite direction, filtering rows of one tag by keys selected
from another, which no single statement can do once the two tags live on
separate databases.

Neither reproduces SQL semantics in Python. The reference traversal is a
primary-key lookup that SQLAlchemy's own ``selectin`` loading performs, and
the filter resolves to a key set before it is applied.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
import sqlalchemy.exc as sa_exc
import sqlalchemy.orm as so
from oa_configurator import (
    Dialect,
    ResolvedCDMDatabase,
    Role,
    statement_schema_tags,
)


class CDMSession(so.Session):
    """Session that binds every statement to the engine hosting its schema tag.

    Routing reads the in-Python ``Table.schema`` tag of the statement's
    tables, so any package's mapped or Core tables route without being
    registered first. A statement spanning both databases is still sent to
    one of them, where the engine's boundary guard refuses it.

    Parameters
    ----------
    resolved : ResolvedCDMDatabase
        Supplies the routing rule through ``route_for_schema_tag``.
    primary, vocab : sqlalchemy.Engine
        The pair from ``create_engines()``.
    **kwargs
        Forwarded to ``sqlalchemy.orm.Session``.
    """

    def __init__(
        self,
        *,
        resolved: ResolvedCDMDatabase,
        primary: sa.Engine,
        vocab: sa.Engine,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._resolved = resolved
        self._primary = primary
        self._vocab = vocab

    def get_bind(
        self,
        mapper: Any = None,
        *,
        clause: Any = None,
        bind: Any = None,
        **kw: Any,
    ) -> sa.Engine | sa.Connection:
        """Engine hosting the tables of *clause*, or else of *mapper*.

        Raises
        ------
        sqlalchemy.exc.UnboundExecutionError
            If neither names a schema-tagged table and the roles use separate
            engines, since no engine can be chosen.
        """
        if bind is not None:
            return bind
        if self._primary is self._vocab:
            return self._primary
        tags = sorted(statement_schema_tags(clause)) if clause is not None else []
        if not tags and mapper is not None:
            tags = sorted(
                table.schema for table in sa.inspect(mapper).mapper.tables if table.schema
            )
        if not tags:
            raise sa_exc.UnboundExecutionError(
                "This session binds each table to the engine hosting it, and this "
                "statement names no schema-tagged table. On split databases, pass "
                "a mapper or tagged clause, or run raw SQL on the engine you mean."
            )
        role_tags = {tag for tag in tags if tag in (Role.PRIMARY.value, Role.VOCAB.value)}
        if Role.VOCAB.value in role_tags:
            selected_tag = Role.VOCAB.value
        elif Role.PRIMARY.value in role_tags:
            selected_tag = Role.PRIMARY.value
        else:
            selected_tag = tags[0]
        return self._resolved.route_for_schema_tag(
            selected_tag, vocab=self._vocab, primary=self._primary
        )


def check_engine_pair(
    resolved: ResolvedCDMDatabase, *, primary: sa.Engine, vocab: sa.Engine
) -> None:
    """Raise unless *primary* and *vocab* address *resolved*'s own role connections.

    Raises
    ------
    ValueError
        If either engine addresses another connection, e.g. when the two are
        swapped.
    """
    for role, engine in ((Role.PRIMARY, primary), (Role.VOCAB, vocab)):
        if role not in resolved.roles_on_connection(engine):
            raise ValueError(
                f"The {role.value} engine does not address {resolved.name!r}'s "
                f"{role.value} connection. Pass the pair from create_engines()."
            )


def cdm_sessionmaker(
    resolved: ResolvedCDMDatabase,
    *,
    primary: sa.Engine,
    vocab: sa.Engine,
    **kwargs: Any,
) -> so.sessionmaker[CDMSession]:
    """Session factory whose sessions route every table to its hosting engine.

    Reference attributes declared on the ``ReferenceContext`` mixins load
    with ``lazy="selectin"``, so resolving them issues one further query per
    referenced table against whichever engine holds it, batched by distinct
    key rather than per row.

    Parameters
    ----------
    resolved : ResolvedCDMDatabase
    primary, vocab : sqlalchemy.Engine
        The pair from ``create_engines()``.
    **kwargs
        Forwarded to ``sqlalchemy.orm.sessionmaker``.

    Raises
    ------
    ValueError
        If *primary* or *vocab* does not address the connection *resolved*
        hosts that role on, e.g. when the two are swapped.
    """
    check_engine_pair(resolved, primary=primary, vocab=vocab)
    return so.sessionmaker(
        class_=CDMSession, resolved=resolved, primary=primary, vocab=vocab, **kwargs
    )


def filter_by_keys(
    column: sa.SQLColumnExpression[Any],
    *,
    keys_select: sa.Select[Any],
    session: so.Session,
) -> sa.ColumnElement[bool]:
    """Predicate restricting *column* to the keys *keys_select* returns.

    Emits a plain subquery when *session* sends both to one engine, letting
    the database do the work; ``create_engines()`` returns one engine for both
    roles whenever they share a database. Only when they are genuinely separate is the key set read
    out through *session* and inlined, as a single array parameter where the
    dialect supports one and as an expanded list otherwise.

    Parameters
    ----------
    column : sqlalchemy.SQLColumnExpression
        Column to restrict.
    keys_select : sqlalchemy.Select
        Selects exactly one column, the keys to keep.
    session : sqlalchemy.orm.Session
        Decides where each side runs. A session from :func:`cdm_sessionmaker`
        on a split deployment reads the keys inside its own transaction.

    Returns
    -------
    sqlalchemy.ColumnElement[bool]
        Usable directly in a ``WHERE`` clause.

    Notes
    -----
    An array parameter has no practical ceiling and needs no write access,
    so nothing is materialised into a temporary table. An expanded list is
    bounded by the driver's parameter limit, which on PostgreSQL is reached
    around 65,000 keys and is why the array form is preferred there.
    """
    column_select = sa.select(column)
    bind = session.get_bind(clause=column_select)
    keys_bind = session.get_bind(clause=keys_select)
    if bind.engine is keys_bind.engine:
        return column.in_(keys_select)

    keys = session.execute(keys_select).scalars().all()
    if not keys:
        return sa.false()
    if bind.engine.dialect.name == Dialect.POSTGRESQL:
        key_type = column_select.selected_columns[0].type
        return column == sa.any_(sa.literal(list(keys), sa.ARRAY(key_type)))
    return column.in_(list(keys))
