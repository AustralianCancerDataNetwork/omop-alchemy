from __future__ import annotations

from hashlib import sha1
from typing import Union, Mapping, Tuple, TypedDict, Any, cast
import sqlalchemy as sa

from sqlalchemy.sql.schema import SchemaItem
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy import Column

TableArg = Union[
    SchemaItem,                  # Index, Constraint, etc.
    Mapping[str, object],        # __table_args__ dict
    Tuple["TableArg", ...],      # nested tuples
]

class OmopInfoDict(TypedDict, total=False):
    omop_cluster: bool | str

ColumnLike = Union[str, Column[object], ColumnElement[object]]

OMOP_CLUSTER_INDEX_INFO_KEY = "omop_cluster"
OMOP_IDENTIFIER_MAX_LENGTH = 63

"""
Helper functions for defining indexes and table options in OMOP CDM ORM models.

Thin abstraction over SQLAlchemy `__table_args__` to support optional metadata 
(e.g. clustering) that may only be applied in certain database backends.

Clustering directives are stored in the SQLAlchemy `info` dictionary and are
intended to be interpreted by external maintenance tooling rather than enforced
at table definition time.

Use maintenance cli to apply indices and clustering to existing tables.
"""

def _truncate_identifier(name: str, *, max_length: int = OMOP_IDENTIFIER_MAX_LENGTH) -> str:
    if len(name) <= max_length:
        return name

    digest = sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"{name[: max_length - len(digest) - 1]}_{digest}"


def _index_column_name(column: ColumnLike) -> str:
    if isinstance(column, str):
        return column

    for attr_name in ("name", "key"):
        value = getattr(column, attr_name, None)
        if isinstance(value, str) and value:
            return value

    raise TypeError(f"Cannot derive index column name from {column!r}")


def omop_index_name(
    table_name: str,
    *columns: ColumnLike,
    unique: bool = False,
) -> str:
    prefix = "uq" if unique else "ix"
    parts = [prefix, table_name, *(_index_column_name(column) for column in columns)]
    return _truncate_identifier("_".join(parts))


def omop_index(
    table_or_name: str,
    *columns: ColumnLike,
    unique: bool = False,
    cluster: bool = False,
    name: str | None = None,
) -> sa.Index:

    """
    Construct a SQLAlchemy Index with optional OMOP-specific metadata.

    Parameters
    ----------
    table_or_name:
        Preferred usage: table name used to generate a standard index name.
        Legacy usage: explicit index name such as ``ix_person_gender_concept_id``.
    columns:
        Column names or SQLAlchemy column expressions.
    unique:
        Whether the index enforces uniqueness.
    cluster:
        If True, annotate the index as the preferred clustering index via
        the `info` dictionary. This does not apply clustering directly.
    name:
        Optional explicit index name override.

    Returns
    -------
    sa.Index
        Configured SQLAlchemy Index object.

    Notes
    -----
    Clustering is not applied automatically. The `info` flag is intended for
    downstream maintenance processes (e.g. PostgreSQL CLUSTER).
    """
    if name is not None:
        index_name = name
    elif table_or_name.startswith(("ix_", "idx_", "uq_", "pk_")) and columns:
        index_name = table_or_name
    else:
        index_name = omop_index_name(
            table_or_name,
            *columns,
            unique=unique,
        )

    info = {OMOP_CLUSTER_INDEX_INFO_KEY: True} if cluster else None
    return sa.Index(
        index_name,
        *columns,
        unique=unique,
        info=info,
    )

def omop_primary_key_index_name(table_name: str) -> str:
    """
    Generate a conventional primary key index name.

    Parameters
    ----------
    table_name:
        Name of the table.

    Returns
    -------
    str
        Index name in the form ``pk_<table_name>``.
    """
    return _truncate_identifier(f"pk_{table_name}")

def omop_table_options(
    *,
    cluster_on: str | None = None,
) -> dict[str, object]:
    """
    Construct a `__table_args__` options dictionary with optional clustering metadata.

    Parameters
    ----------
    cluster_on:
        Name of the index to use for clustering. If None, no clustering metadata
        is included.

    Returns
    -------
    dict[str, object]
        Table options dictionary suitable for inclusion in `__table_args__`.

    Notes
    -----
    The clustering directive is stored in the `info` dictionary and must be
    applied by external tooling.
    """
    if cluster_on is None:
        return {}
    return {
        "info": {
            OMOP_CLUSTER_INDEX_INFO_KEY: cluster_on,
        }
    }

def _normalize_mapping(m: Mapping[Any, Any]) -> dict[str, object]:
    return {str(k): v for k, v in m.items()}

def merge_table_args(*parts: TableArg) -> tuple[TableArg, ...]:

    """
    Merge and normalize SQLAlchemy `__table_args__` components.

    This function accepts a mixture of:
    - Schema items (e.g. Index, Constraint)
    - Option dictionaries
    - Nested tuples of the above

    It performs the following transformations:
    - Flattens nested tuples
    - Merges multiple option dictionaries into a single dictionary
    - Merges `info` dictionaries separately to avoid overwriting
    - Preserves ordering of schema items
    - Appends the merged options dictionary (if any) as the final element

    Parameters
    ----------
    parts:
        Components of `__table_args__`, possibly nested.

    Returns
    -------
    tuple[TableArg, ...]
        Normalized tuple suitable for assignment to `__table_args__`.

    Notes
    -----
    - Only a single options dictionary is emitted in the result.
    - Later option values override earlier ones.
    - `info` dictionaries are merged shallowly.
    """
    items: list[TableArg] = []
    merged_options: dict[str, object] = {}
    merged_info: dict[str, object] = {}

    def consume(part: TableArg) -> None:
        # A Constraint/Index built from unresolved string column names 
        # (e.g. PrimaryKeyConstraint("col_a")) has an empty .columns 
        # collection and is falsy until it's attached to a Table,
        #  which would silently drop it from __table_args__.
        if part is None:
            return

        if isinstance(part, Mapping):
            options = _normalize_mapping(cast(Mapping[Any, Any], part))
            info = options.pop("info", None)
            if isinstance(info, Mapping):
                merged_info.update(_normalize_mapping(cast(Mapping[Any, Any], info)))
            merged_options.update(_normalize_mapping(options))
            return

        if isinstance(part, tuple):
            for item in part:
                consume(item)
            return

        items.append(part)

    for part in parts:
        consume(part)

    if merged_info:
        merged_options["info"] = merged_info
    if merged_options:
        items.append(merged_options)

    return tuple(items)
