"""Index management commands for dropping and recreating ORM-defined secondary indexes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError
import typer

from oa_configurator import ensure_schema, supports_schemas

from omop_alchemy.cdm.base.indexing import OMOP_CLUSTER_INDEX_INFO_KEY

from ..backends import resolve_backend, backend_supports
from ._cli_utils import MAINTENANCE_SCHEMA, Status, dry_label, dry_status, omop_command
from .tables import (
    MaintenanceTable,
    TableCategory,
    select_omop_tables,
)
from .ui import (
    console,
    render_index_note,
    render_index_results,
    render_index_summary,
)


@dataclass(frozen=True)
class IndexTarget:
    """An ORM-defined index that currently exists in the target database."""

    table_name: str
    category: TableCategory
    index_name: str
    column_names: tuple[str, ...]
    unique: bool
    clustered: bool


@dataclass(frozen=True)
class IndexManagementResult(IndexTarget):
    """Outcome of creating or dropping one ORM-defined index, or clustering a table."""
    operation: str
    enable: bool
    status: Status
    detail: str


def _is_plain_index(reflected: Mapping[str, Any]) -> bool:
    """Determine whether a reflected index is a plain, safely-manageable index.

    A plain index has no expression components, no partial predicate, uses the
    default (btree) access method, and does not back a UNIQUE or PRIMARY KEY
    constraint. Only plain indexes are safe to treat as a faithful equivalent
    of an ORM-defined index, and safe to capture and restore: PostgreSQL
    refuses to DROP INDEX on a constraint-backed index, and partial, expression,
    or non-btree indexes can't be faithfully reconstructed from a plain column
    list alone.

    Parameters
    ----------
    reflected : Mapping[str, Any]
        A single index dict as returned by sqlalchemy.engine.Inspector.get_indexes().

    Returns
    -------
    bool
        True if the index is plain (a faithful, manageable equivalent), False otherwise.
    """
    column_names = reflected.get("column_names") or []
    if any(name is None for name in column_names):
        return False
    if reflected.get("expressions"):
        return False
    if reflected.get("duplicates_constraint"):
        return False
    dialect_options = reflected.get("dialect_options") or {}
    if dialect_options.get("postgresql_where"):
        return False
    if dialect_options.get("postgresql_using"):
        return False
    return True


def _find_equivalent_index(
    existing_indexes: Sequence[Mapping[str, Any]],
    column_names: tuple[str, ...],
    unique: bool | None,
) -> str | None:
    """Find the physical name of a plain existing index matching a column set.

    Parameters
    ----------
    existing_indexes : Sequence[Mapping[str, Any]]
        Reflected indexes for one table, as returned by Inspector.get_indexes().
    column_names : tuple[str, ...]
        Column names an ORM-defined index expects, in order. Matching is
        order-sensitive since composite index column order affects usability.
    unique : bool or None
        Uniqueness flag an ORM-defined index expects. None skips the
        uniqueness check entirely. Used when resolving a CLUSTER target,
        where uniqueness is irrelevant to whether an index can serve as the
        physical sort key (e.g. the official OHDSI CDM DDL always clusters
        on a plain, non-unique index even for tables whose ORM-declared
        cluster target is the primary key's own unique index).

    Returns
    -------
    str or None
        Physical name of the first plain existing index whose column_names
        tuple (and unique flag, unless unique is None) match, or None if no
        equivalent exists.
    """
    for reflected in existing_indexes:
        if not _is_plain_index(reflected):
            continue
        if tuple(reflected.get("column_names") or ()) != column_names:
            continue
        if unique is not None and bool(reflected.get("unique")) != unique:
            continue
        return str(reflected["name"])
    return None


def _find_shape_conflict(
    existing_indexes: Sequence[Mapping[str, Any]],
    column_names: tuple[str, ...],
    unique: bool,
) -> Mapping[str, Any] | None:
    """Find a non-plain existing index covering the same columns.

    Like _find_equivalent_index but for a match that can't be safely treated
    as equivalent (expression, partial, non-btree, or constraint-backed). Used
    only to explain why such an index is left alone rather than captured.

    Parameters
    ----------
    existing_indexes : Sequence[Mapping[str, Any]]
        Reflected indexes for one table, as returned by Inspector.get_indexes().
    column_names : tuple[str, ...]
        Column names an ORM-defined index expects, in order.
    unique : bool
        Uniqueness flag an ORM-defined index expects.

    Returns
    -------
    Mapping[str, Any] or None
        The reflected index dict of the conflicting index, or None if no
        such index exists.
    """
    for reflected in existing_indexes:
        column_names_reflected = reflected.get("column_names") or []
        if any(name is None for name in column_names_reflected):
            continue
        if tuple(column_names_reflected) != column_names:
            continue
        if bool(reflected.get("unique")) != unique:
            continue
        if _is_plain_index(reflected):
            continue
        return reflected
    return None


def _describe_shape_conflict(reflected: Mapping[str, Any]) -> str:
    """Describe why a reflected index can't be safely captured or treated as equivalent.

    Parameters
    ----------
    reflected : Mapping[str, Any]
        A reflected index dict, as returned by _find_shape_conflict.

    Returns
    -------
    str
        Human-readable reason(s), comma-separated.
    """
    dialect_options = reflected.get("dialect_options") or {}
    reasons: list[str] = []
    if reflected.get("duplicates_constraint"):
        reasons.append("backs a UNIQUE/PRIMARY KEY constraint")
    if dialect_options.get("postgresql_where"):
        reasons.append("has a partial WHERE predicate")
    if dialect_options.get("postgresql_using"):
        reasons.append(f"uses non-btree access method '{dialect_options['postgresql_using']}'")
    if not reasons:
        reasons.append("has an unsupported definition")
    return ", ".join(reasons)


# ── Foreign index capture/restore bookkeeping ───────────────────────────────────

_DROPPED_INDEXES_TABLE_NAME = "dropped_indexes"
_DROPPED_INDEXES_UNIQUE_CONSTRAINT_NAME = "uq_dropped_indexes_table_columns"


def _schema_key(db_schema: str | None) -> str:
    """Normalize db_schema to a non-null string for use in the bookkeeping table.

    SQL UNIQUE constraints treat every NULL as distinct from every other NULL,
    so a nullable db_schema column would silently defeat uniqueness whenever
    db_schema is None (SQLite always, and PostgreSQL whenever no explicit
    schema is configured). As a result, two captures for the same table/column-set 
    could both succeed instead of the second being rejected.

    Parameters
    ----------
    db_schema : str or None
        Schema name, or None for "no explicit schema".

    Returns
    -------
    str
        db_schema unchanged, or "" when db_schema is None.
    """
    return db_schema or ""


def get_bookkeeping_schema(connection: sa.Connection) -> str | None:
    """Return the reserved schema name for the dropped-index bookkeeping table.

    Parameters
    ----------
    connection : sqlalchemy.Connection
        The connection the bookkeeping table would be created on.

    Returns
    -------
    str or None
        MAINTENANCE_SCHEMA on a dialect with a genuine multi-schema concept
        (like PostgreSQL), or None on one that doesn't (like SQLite).
    """
    if supports_schemas(connection):
        return MAINTENANCE_SCHEMA
    return None


def _dropped_indexes_table(bookkeeping_schema: str | None) -> sa.Table:
    """Build the dropped-index bookkeeping table definition.

    disable drops indexes to speed up bulk loads. If the only index covering
    an ORM-defined column set is a foreign (non-OMOP_Alchemy) index, e.g. one
    created by the official OHDSI CDM DDL script, dropping it still gives the
    bulk-load speed benefit, but its definition must be captured somewhere that
    survives across separate CLI invocations, since disable and enable are
    documented as usable as two separate commands, not just paired within a
    single process. This table records that definition in a reserved schema
    before the foreign index is dropped, so a later enable call can recreate
    it under its original name. db_schema is part of the row identity so two
    same-named tables in different schemas of one database never collide.

    Parameters
    ----------
    bookkeeping_schema : str or None
        Schema to qualify the table with, from get_bookkeeping_schema().

    Returns
    -------
    sqlalchemy.Table
        The (unbound) table definition. Not yet created in the database.
    """
    metadata = sa.MetaData()
    return sa.Table(
        _DROPPED_INDEXES_TABLE_NAME,
        metadata,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("table_name", sa.String(128), nullable=False),
        sa.Column("db_schema", sa.String(128), nullable=False),
        sa.Column("index_name", sa.String(128), nullable=False),
        sa.Column("column_names_json", sa.Text, nullable=False),
        sa.Column("is_unique", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("captured_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "table_name", "db_schema", "column_names_json", "is_unique",
            name=_DROPPED_INDEXES_UNIQUE_CONSTRAINT_NAME,
        ),
        schema=bookkeeping_schema,
    )


def _record_captured_index(
    connection: sa.Connection,
    *,
    table_name: str,
    db_schema: str | None,
    index_name: str,
    column_names: tuple[str, ...],
    unique: bool,
) -> bool:
    """Capture a foreign index's definition into the bookkeeping table before dropping it.

    Returns False, recording nothing, if this table/schema/column-set already
    has a pending capture awaiting restore, e.g. a second disable run,
    without an intervening enable, that finds a different foreign index than
    the one already captured. The bookkeeping table's unique constraint only
    allows one pending capture per table/schema/column-set/uniqueness, since
    only one original name could ever be restored to that slot; the caller
    must leave the second index in place rather than dropping something it can
    no longer track.

    Parameters
    ----------
    connection : sqlalchemy.Connection
        Open connection/transaction the capture is recorded on.
    table_name : str
        Name of the table the foreign index belongs to.
    db_schema : str or None
        Schema the target table lives in, so captures from different schemas
        of a same-named table never collide.
    index_name : str
        Original physical name of the foreign index being captured.
    column_names : tuple[str, ...]
        Column names the foreign index covers, in order.
    unique : bool
        Uniqueness flag of the foreign index.

    Returns
    -------
    bool
        True if the capture was recorded, False if a pending capture already
        existed for this table/schema/column-set/uniqueness.
    """
    bookkeeping_schema = get_bookkeeping_schema(connection)
    ensure_schema(connection, bookkeeping_schema)
    bookkeeping_table = _dropped_indexes_table(bookkeeping_schema)
    bookkeeping_table.create(bind=connection, checkfirst=True)

    savepoint = connection.begin_nested()
    try:
        connection.execute(
            bookkeeping_table.insert(),
            {
                "table_name": table_name,
                "db_schema": _schema_key(db_schema),
                "index_name": index_name,
                "column_names_json": json.dumps(list(column_names)),
                "is_unique": unique,
            },
        )
    except IntegrityError as exc:
        savepoint.rollback()
        message = str(exc.orig).lower()
        # Constraint naming differs between backends
        # We are preventing to swallow other unique constraint violations
        # by checking for a specific one
        if _DROPPED_INDEXES_UNIQUE_CONSTRAINT_NAME not in message and _DROPPED_INDEXES_TABLE_NAME not in message:
            raise
        return False
    else:
        savepoint.commit()
        return True


def _peek_captured_index(
    connection: sa.Connection,
    *,
    table_name: str,
    db_schema: str | None,
    column_names: tuple[str, ...],
    unique: bool,
) -> tuple[str | None, sa.Table | None, sa.Row | None]:
    """Look up a pending capture's original index name without restoring or deleting it.

    Read-only counterpart to _restore_captured_index, used to preview what a
    live run would do (a restore, or a capture-conflict) without mutating the
    bookkeeping table.

    Parameters
    ----------
    connection : sqlalchemy.Connection
        Open connection the lookup is performed on.
    table_name : str
        Name of the table the index belongs to.
    db_schema : str or None
        Schema the target table lives in.
    column_names : tuple[str, ...]
        Column names the captured index covers, in order.
    unique : bool
        Uniqueness flag of the captured index.

    Returns
    -------
    restored_index_name : str or None
        The captured index's original physical name, or None if nothing is
        captured for this table/schema/column-set/uniqueness.
    bookkeeping_table : sqlalchemy.Table or None
        The bookkeeping table definition, for use in a later restore or
        delete. None if the bookkeeping table doesn't exist yet.
    row : sqlalchemy.Row or None
        The matched bookkeeping row, for use in a later delete by id. None if
        nothing is captured for this table/schema/column-set/uniqueness.
    """
    bookkeeping_schema = get_bookkeeping_schema(connection)
    inspector = sa.inspect(connection)
    if not inspector.has_table(_DROPPED_INDEXES_TABLE_NAME, schema=bookkeeping_schema):
        return None, None, None

    bookkeeping_table = _dropped_indexes_table(bookkeeping_schema)
    column_names_json = json.dumps(list(column_names))
    row = connection.execute(
        sa.select(bookkeeping_table.c.id, bookkeeping_table.c.index_name).where(
            bookkeeping_table.c.table_name == table_name,
            bookkeeping_table.c.db_schema == _schema_key(db_schema),
            bookkeeping_table.c.column_names_json == column_names_json,
            bookkeeping_table.c.is_unique == unique,
        )
    ).one_or_none()
    return str(row.index_name) if row is not None else None, bookkeeping_table, row


def _restore_captured_index(
    connection: sa.Connection,
    *,
    table_name: str,
    db_schema: str | None,
    column_names: tuple[str, ...],
    unique: bool,
) -> str | None:
    """Recreate a previously captured foreign index under its original name.

    Removes the corresponding bookkeeping row on success.

    Parameters
    ----------
    connection : sqlalchemy.Connection
        Open connection/transaction the index is created on.
    table_name : str
        Name of the table to recreate the index on.
    db_schema : str or None
        Schema the target table lives in.
    column_names : tuple[str, ...]
        Column names the restored index should cover, in order.
    unique : bool
        Uniqueness flag the restored index should have.

    Returns
    -------
    str or None
        The restored physical index name, or None if nothing was captured
        for this table/schema/column-set, or if it was already recreated by
        someone else in the meantime (see Notes).

    Notes
    -----
    If the captured index was already recreated out-of-band (e.g. a concurrent
    enable invocation, or a manually restored backup), create(checkfirst=True)
    can still race with the physical CREATE INDEX statement and raise a
    "relation already exists" DBAPIError; this is caught the same way the
    equivalent race is handled for newly-created ORM-defined indexes, and
    treated as a no-op restore (the bookkeeping row is still cleared, since the
    index is confirmed to exist either way).
    """
    restored_index_name, bookkeeping_table, row = _peek_captured_index(
        connection=connection,
        table_name=table_name,
        db_schema=db_schema,
        column_names=column_names,
        unique=unique,
    )
    if restored_index_name is None or bookkeeping_table is None or row is None:
        return None

    # A lightweight, untyped Table (no autoload_with reflection) is sufficient:
    # CREATE INDEX DDL only needs column names, not real types, PKs, FKs, or
    # constraints. Reflecting the whole table would cost several extra
    # catalog round-trips to fetch metadata this function never uses.
    lightweight_table = sa.Table(
        table_name, sa.MetaData(),
        *(sa.Column(name) for name in column_names),
        schema=db_schema,
    )
    restored_index = sa.Index(
        restored_index_name, *[lightweight_table.c[name] for name in column_names], unique=unique
    )
    savepoint = connection.begin_nested()
    try:
        restored_index.create(bind=connection, checkfirst=True)
    except DBAPIError as exc:
        savepoint.rollback()
        if "already exists" not in str(exc.orig).lower():
            raise
    else:
        savepoint.commit()
    connection.execute(bookkeeping_table.delete().where(bookkeeping_table.c.id == row.id))
    return restored_index_name


def _schema_metadata_indexes(
    tables: list[MaintenanceTable],
) -> dict[tuple[str, str], sa.Index]:
    """Return a (table_name, index_name) -> Index mapping from ORM metadata.

    Index name/columns don't depend on which schema a table is tagged with,
    so this reads straight off each table's own ORM metadata.
    """
    indexes: dict[tuple[str, str], sa.Index] = {}
    for table in tables:
        for index in table.table.indexes:
            indexes[(table.table_name, str(index.name))] = index
    return indexes


def _cluster_target_name(table: MaintenanceTable) -> str | None:
    """Return the name of the ORM-designated cluster index for a table, or None if no cluster target is defined."""
    cluster_indexes = [
        str(index.name)
        for index in table.table.indexes
        if index.info.get(OMOP_CLUSTER_INDEX_INFO_KEY) is True
    ]
    if cluster_indexes:
        return cluster_indexes[0]

    cluster_name = table.table.info.get(OMOP_CLUSTER_INDEX_INFO_KEY)
    if isinstance(cluster_name, str):
        return cluster_name

    return None


def _cluster_column_names(
    table: MaintenanceTable,
    cluster_index_name: str,
) -> tuple[str, ...]:
    """Return the column names of the named cluster index. Falls back to the primary key if the index is not found."""
    for index in table.table.indexes:
        if str(index.name) == cluster_index_name:
            return tuple(column.name for column in index.columns)
    return table.primary_key_names


def _resolve_physical_cluster_name(
    existing_indexes: Sequence[Mapping[str, Any]],
    cluster_index_name: str,
    cluster_columns: tuple[str, ...],
) -> str:
    """Resolve the physical name of a table's cluster-target index.

    The ORM-designated cluster index may not physically exist under its own
    name if the database was pre-indexed by an external script (e.g. the
    OHDSI CDM DDL script), so this falls back to whichever plain index
    actually covers the same columns. Shared by the standalone indexes
    cluster command and manage_indexes()'s cluster step (for the
    primary-key-based cluster target case, which manage_indexes() otherwise
    resolves more precisely via its own per-run physical_index_names tracking
    -- see the comment at its call site).

    Notes
    -----
    Equivalence ignores uniqueness: CLUSTER only needs an index to sort the
    table's physical storage by, and a foreign index doesn't need to be
    unique to serve that role. The official OHDSI CDM DDL always clusters on
    a plain, non-unique index even for tables whose ORM-declared cluster
    target is the primary key's own unique index (e.g. `idx_person_id`,
    non-unique, vs. our own `pk_person`), so requiring a uniqueness match
    here would fail to recognize exactly the databases this exists to support.

    Parameters
    ----------
    existing_indexes : Sequence[Mapping[str, Any]]
        Reflected indexes for the table, as returned by Inspector.get_indexes().
    cluster_index_name : str
        The ORM's own name for the cluster-target index.
    cluster_columns : tuple[str, ...]
        Column names the cluster-target index covers, in order.

    Returns
    -------
    str
        cluster_index_name if it physically exists under that name, otherwise
        the physical name of a plain equivalent index if one is found,
        otherwise cluster_index_name unchanged.
    """
    existing_names = {index["name"] for index in existing_indexes}
    if cluster_index_name in existing_names:
        return cluster_index_name
    equivalent_name = _find_equivalent_index(existing_indexes, cluster_columns, None)
    return equivalent_name if equivalent_name is not None else cluster_index_name


def collect_index_targets(
    engine: sa.Engine,
    *,
    db_schema: str | None = None,
    vocabulary_included: bool = False,
) -> list[IndexTarget]:
    """List ORM-defined indexes that currently exist in the target database."""
    inspector = sa.inspect(engine)
    selected_tables = select_omop_tables(vocabulary_included=vocabulary_included)

    targets: list[IndexTarget] = []
    for table in selected_tables:
        if not inspector.has_table(table.table_name, schema=db_schema):
            continue

        existing_indexes = inspector.get_indexes(table.table_name, schema=db_schema)
        existing_index_names = {index["name"] for index in existing_indexes}

        for metadata_index in sorted(table.table.indexes, key=lambda idx: idx.name or ""):
            column_names = tuple(column.name for column in metadata_index.columns)
            unique = bool(metadata_index.unique)
            if metadata_index.name in existing_index_names:
                physical_name = str(metadata_index.name)
            else:
                equivalent_name = _find_equivalent_index(existing_indexes, column_names, unique)
                if equivalent_name is None:
                    continue
                physical_name = equivalent_name

            targets.append(
                IndexTarget(
                    table_name=table.table_name,
                    category=table.category,
                    index_name=physical_name,
                    column_names=column_names,
                    unique=unique,
                    clustered=metadata_index.info.get(OMOP_CLUSTER_INDEX_INFO_KEY) is True,
                )
            )

    return targets

@dataclass(frozen=True)
class _IndexOutcome:
    """Resolved outcome of processing one ORM-defined index in manage_indexes().

    Built directly at the point each outcome is determined, in the live-run
    or dry-run branch -- there is exactly one place that decides status,
    detail, and physical_name for a given case, not a separate translation
    step that could fall out of sync with the branch that determined it.

    Parameters
    ----------
    status : Status
        The status to report for this index.
    detail : str
        Human-readable detail text to report for this index.
    physical_name : str
        The physical index name to report. The ORM's own index_name unless a
        foreign name was captured, restored, or found equivalent.
    """

    status: Status
    detail: str
    physical_name: str


def manage_indexes(
    engine: sa.Engine,
    *,
    enable: bool,
    db_schema: str | None = None,
    vocabulary_included: bool = False,
    dry_run: bool = False,
    cluster: bool = True,
) -> list[IndexManagementResult]:
    """Create or drop all ORM-defined indexes. CLUSTERs tables when enabling and cluster=True."""
    backend = resolve_backend(engine)
    inspector = sa.inspect(engine)
    selected_tables = select_omop_tables(vocabulary_included=vocabulary_included)
    metadata_indexes = _schema_metadata_indexes(selected_tables)
    clustering_supported = backend_supports(backend, "cluster_table")

    results: list[IndexManagementResult] = []

    for table in selected_tables:
        if not inspector.has_table(table.table_name, schema=db_schema):
            continue

        existing_indexes = inspector.get_indexes(table.table_name, schema=db_schema)
        existing_index_names = {index["name"] for index in existing_indexes}

        created_any = False
        clustered_now = False
        physical_index_names: dict[str, str] = {}

        for metadata_index in sorted(table.table.indexes, key=lambda idx: idx.name or ""):
            index_name = str(metadata_index.name)
            column_names = tuple(column.name for column in metadata_index.columns)
            unique = bool(metadata_index.unique)
            exists = index_name in existing_index_names
            should_apply = (
                not enable
            ) or (
                enable and not exists
            )

            if not should_apply:
                physical_index_names[index_name] = index_name
                continue

            schema_index = metadata_indexes[(table.table_name, index_name)]
            # Plain create/drop succeeding is the common case for both live and
            # dry runs, so it's the default outcome; every branch below only
            # overrides it for a foreign-index or already-in-place case.
            outcome = _IndexOutcome(
                status=dry_status(dry_run),
                detail=dry_label(
                    dry_run,
                    planned="metadata-defined index would be dropped" if not enable else "metadata-defined index would be created",
                    applied="metadata-defined index dropped" if not enable else "metadata-defined index created",
                ),
                physical_name=index_name,
            )

            # Each index gets its own connection: a transaction when actually
            # mutating (not dry_run, so WAL is committed and checkpointable
            # before the next index build begins), a plain read-only
            # connection when only previewing.
            connection_factory = engine.begin if not dry_run else engine.connect
            with connection_factory() as connection:
                if not enable:
                    if not dry_run:
                        existed_before_drop = backend.index_exists(connection, index_name)
                    else:
                        existed_before_drop = exists
                    if not existed_before_drop:
                        # Index under a different naming scheme than ours
                        equivalent_name = _find_equivalent_index(existing_indexes, column_names, unique)
                        if equivalent_name is not None:
                            if not dry_run:
                                captured = _record_captured_index(
                                    connection,
                                    table_name=table.table_name, db_schema=db_schema,
                                    index_name=equivalent_name,
                                    column_names=column_names, unique=unique,
                                )
                            else:
                                pending_capture, _, _ = _peek_captured_index(
                                    connection,
                                    table_name=table.table_name, db_schema=db_schema,
                                    column_names=column_names, unique=unique,
                                )
                                captured = pending_capture is None
                            if captured:
                                if not dry_run:
                                    backend.drop_index_if_exists(connection, equivalent_name)
                                outcome = _IndexOutcome(
                                    status=dry_status(dry_run, Status.CAPTURED),
                                    detail=dry_label(
                                        dry_run,
                                        planned=f"foreign index '{equivalent_name}' would be captured and dropped for bulk load",
                                        applied=f"foreign index '{equivalent_name}' captured and dropped for bulk load",
                                    ),
                                    physical_name=equivalent_name,
                                )
                            else:
                                # A different foreign index for this table/column-set is
                                # already captured and awaiting restore. Leave this one
                                # in place rather than dropping something we can no
                                # longer track.
                                outcome = _IndexOutcome(
                                    status=Status.WARNING,
                                    detail=dry_label(
                                        dry_run,
                                        planned=(
                                            f"foreign index '{equivalent_name}' would be left in place: a different "
                                            "foreign index for this table/column-set is already captured and "
                                            "awaiting restore"
                                        ),
                                        applied=(
                                            f"foreign index '{equivalent_name}' left in place: a different "
                                            "foreign index for this table/column-set is already captured and "
                                            "awaiting restore"
                                        ),
                                    ),
                                    physical_name=equivalent_name,
                                )
                        else:
                            conflict = _find_shape_conflict(existing_indexes, column_names, unique)
                            if conflict is not None:
                                conflict_name = str(conflict["name"])
                                outcome = _IndexOutcome(
                                    status=Status.WARNING,
                                    detail=dry_label(
                                        dry_run,
                                        planned=f"foreign index '{conflict_name}' {_describe_shape_conflict(conflict)}; would be left in place",
                                        applied=f"foreign index '{conflict_name}' {_describe_shape_conflict(conflict)}; left in place",
                                    ),
                                    physical_name=conflict_name,
                                )
                            else:
                                outcome = _IndexOutcome(
                                    status=Status.SKIPPED,
                                    detail="metadata-defined index already absent (skipped)",
                                    physical_name=index_name,
                                )
                    elif not dry_run:
                        backend.drop_index_if_exists(connection, index_name)
                        # outcome stays default: applied / "metadata-defined index dropped"
                    # dry-run, existed_before_drop True: outcome stays default ("would be dropped")
                else:
                    if not dry_run:
                        restored_name = _restore_captured_index(
                            connection,
                            table_name=table.table_name, db_schema=db_schema,
                            column_names=column_names, unique=unique,
                        )
                    else:
                        restored_name, _, _ = _peek_captured_index(
                            connection,
                            table_name=table.table_name, db_schema=db_schema,
                            column_names=column_names, unique=unique,
                        )
                    if restored_name is not None:
                        if not dry_run:
                            created_any = True
                        outcome = _IndexOutcome(
                            status=dry_status(dry_run, Status.RESTORED),
                            detail=dry_label(
                                dry_run,
                                planned=f"foreign index '{restored_name}' would be restored from bulk-load capture",
                                applied=f"foreign index '{restored_name}' restored from bulk-load capture",
                            ),
                            physical_name=restored_name,
                        )
                    else:
                        equivalent_name = _find_equivalent_index(existing_indexes, column_names, unique)
                        if equivalent_name is not None:
                            outcome = _IndexOutcome(
                                status=Status.SKIPPED,
                                detail=dry_label(
                                    dry_run,
                                    planned=f"equivalent foreign index '{equivalent_name}' already provides this coverage (would skip creation)",
                                    applied=f"equivalent foreign index '{equivalent_name}' already provides this coverage (skipped)",
                                ),
                                physical_name=equivalent_name,
                            )
                        elif not dry_run:
                            savepoint = connection.begin_nested()
                            try:
                                schema_index.create(bind=connection, checkfirst=True)
                            except DBAPIError as exc:
                                savepoint.rollback()
                                if "already exists" not in str(exc.orig).lower():
                                    raise
                                outcome = _IndexOutcome(
                                    status=Status.SKIPPED,
                                    detail="metadata-defined index already exists (skipped)",
                                    physical_name=index_name,
                                )
                            else:
                                savepoint.commit()
                                created_any = True
                                # outcome stays default: applied / "metadata-defined index created"
                        # dry-run, no restore, no equivalent: outcome stays default ("would be created")

            physical_name = outcome.physical_name
            physical_index_names[index_name] = physical_name
            results.append(
                IndexManagementResult(
                    operation="index",
                    table_name=table.table_name,
                    category=table.category,
                    index_name=physical_name,
                    column_names=column_names,
                    unique=unique,
                    clustered=metadata_index.info.get(OMOP_CLUSTER_INDEX_INFO_KEY) is True,
                    enable=enable,
                    status=outcome.status,
                    detail=outcome.detail,
                )
            )

        # Clustering for perfomance is a separate operation from index creation
        if enable:
            cluster_index_name = _cluster_target_name(table)
            if cluster_index_name is not None:
                cluster_columns = _cluster_column_names(table, cluster_index_name)
                if cluster_index_name in physical_index_names:
                    # Resolved authoritatively from what actually happened in this
                    # run's per-index loop (own name, captured, restored, or a
                    # skip-equivalent) -- more precise than re-deriving from the
                    # now-stale existing_indexes snapshot, since e.g. a
                    # just-restored index wouldn't appear in it.
                    physical_cluster_name = physical_index_names[cluster_index_name]
                else:
                    # Primary-key-based cluster target: never entered the per-index
                    # loop, so resolve it the same way the standalone `indexes
                    # cluster` command does.
                    physical_cluster_name = _resolve_physical_cluster_name(
                        existing_indexes,
                        cluster_index_name,
                        cluster_columns,
                    )
                if not clustering_supported or not cluster:
                    results.append(
                        IndexManagementResult(
                            operation="cluster",
                            table_name=table.table_name,
                            category=table.category,
                            index_name=physical_cluster_name,
                            column_names=cluster_columns,
                            unique=False,
                            clustered=True,
                            enable=enable,
                            status=Status.SKIPPED,
                            detail=(
                                f"cluster metadata present but unsupported on {backend.name}"
                                if not clustering_supported
                                else "clustering skipped (run 'indexes cluster' to apply)"
                            ),
                        )
                    )
                else:
                    if not dry_run:
                        with engine.begin() as connection:
                            backend.cluster_table(connection, table.table_name, physical_cluster_name)
                        clustered_now = True

                    results.append(
                        IndexManagementResult(
                            operation="cluster",
                            table_name=table.table_name,
                            category=table.category,
                            index_name=physical_cluster_name,
                            column_names=cluster_columns,
                            unique=False,
                            clustered=True,
                            enable=enable,
                            status=dry_status(dry_run),
                            detail=dry_label(dry_run, "table would be clustered using ORM-defined metadata", "table clustered using ORM-defined metadata"),
                        )
                    )

        if not dry_run and (created_any or clustered_now):
            with engine.connect() as connection:
                backend.analyze_table(connection, table.table_name)
                connection.commit()

    return results


app = typer.Typer(
    help="Manage ORM-defined secondary indexes.",
    rich_markup_mode="rich",
)


@app.command("disable")
@omop_command("indexes disable", dry_run=True)
def disable_indexes_command(
    conn,
    engine,
    vocabulary_included: bool = typer.Option(
        False,
        "--vocab/--no-vocab",
        help="Include OMOP vocabulary tables in the selection.",
    ),
    dry_run: bool = False,
) -> None:
    """Drop all ORM-defined secondary indexes from the target database. Useful before bulk data loads."""
    with console.status("Managing metadata-defined indexes..."):
        results = manage_indexes(
            engine,
            enable=False,
            db_schema=conn.db_schema,
            vocabulary_included=vocabulary_included,
            dry_run=dry_run,
        )
    console.print(render_index_results(results))
    console.print(render_index_summary(results, dry_run=dry_run))
    console.print(render_index_note(enable=False))


@app.command("enable")
@omop_command("indexes enable", dry_run=True)
def enable_indexes_command(
    conn,
    engine,
    vocabulary_included: bool = typer.Option(
        False,
        "--vocab/--no-vocab",
        help="Include OMOP vocabulary tables in the selection.",
    ),
    cluster: bool = typer.Option(
        True,
        "--cluster/--no-cluster",
        help="Also CLUSTER tables using their ORM-designated cluster index. Use --no-cluster to skip the full heap rewrite on large vocabulary tables.",
    ),
    dry_run: bool = False,
) -> None:
    """Recreate all ORM-defined secondary indexes. Also CLUSTERs tables on PostgreSQL where metadata specifies it.

    Note: CLUSTER rewrites the full heap and requires ~2× the table size in free disk space.
    Pass --no-cluster to create/recreate indexes only, or run 'indexes cluster' as a
    separate step once you've confirmed sufficient disk headroom for large vocabulary tables.
    """
    with console.status("Managing metadata-defined indexes..."):
        results = manage_indexes(
            engine,
            enable=True,
            db_schema=conn.db_schema,
            vocabulary_included=vocabulary_included,
            dry_run=dry_run,
            cluster=cluster,
        )
    console.print(render_index_results(results))
    console.print(render_index_summary(results, dry_run=dry_run))
    console.print(render_index_note(enable=True))


@app.command("cluster")
@omop_command("indexes cluster", dry_run=True)
def cluster_tables_command(
    conn,
    engine,
    vocabulary_included: bool = typer.Option(
        False,
        "--vocab/--no-vocab",
        help="Include OMOP vocabulary tables in the selection.",
    ),
    dry_run: bool = False,
) -> None:
    """CLUSTER tables using their ORM-designated cluster index.

    Physically rewrites table data sorted by the cluster index for improved sequential-scan
    performance. Requires approximately 2× the table size in free disk space per table.

    Run this after 'indexes enable' once you have confirmed sufficient disk headroom.
    On Docker, check Docker Desktop → Resources → Virtual Disk Limit before running on
    vocabulary tables (concept_ancestor alone needs ~5 GB free).
    """
    backend = resolve_backend(engine)
    if not backend_supports(backend, "cluster_table"):
        console.print(f"[yellow]Clustering is not supported on {backend.name}.[/yellow]")
        raise typer.Exit(0)

    inspector = sa.inspect(engine)
    selected_tables = select_omop_tables(vocabulary_included=vocabulary_included)
    results: list[IndexManagementResult] = []

    for table in selected_tables:
        if not inspector.has_table(table.table_name, schema=conn.db_schema):
            continue

        cluster_index_name = _cluster_target_name(table)
        if cluster_index_name is None:
            continue

        cluster_columns = _cluster_column_names(table, cluster_index_name)
        existing_indexes = inspector.get_indexes(table.table_name, schema=conn.db_schema)
        physical_cluster_name = _resolve_physical_cluster_name(
            existing_indexes,
            cluster_index_name,
            cluster_columns,
        )

        if not dry_run:
            with engine.begin() as connection:
                backend.cluster_table(connection, table.table_name, physical_cluster_name)
            with engine.connect() as connection:
                backend.analyze_table(connection, table.table_name)
                connection.commit()

        results.append(
            IndexManagementResult(
                operation="cluster",
                table_name=table.table_name,
                category=table.category,
                index_name=physical_cluster_name,
                column_names=cluster_columns,
                unique=False,
                clustered=True,
                enable=True,
                status=dry_status(dry_run),
                detail=dry_label(dry_run, "table would be clustered and analyzed", "table clustered and analyzed"),
            )
        )

    console.print(render_index_results(results))
    console.print(render_index_summary(results, dry_run=dry_run))
