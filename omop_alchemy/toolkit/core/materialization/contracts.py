"""Side-effect-free contracts for PostgreSQL materialized views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy.sql.selectable import SelectBase


def _require_identifier(value: str, *, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_distinct(values: tuple[str, ...], *, field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")


@dataclass(frozen=True, slots=True)
class MaterializedViewTarget:
    """The schema and name that identify one materialized view."""

    schema: str
    name: str

    def __post_init__(self) -> None:
        _require_identifier(self.schema, field_name="schema")
        _require_identifier(self.name, field_name="name")


@dataclass(frozen=True, slots=True)
class MaterializedViewIndex:
    """A simple column index required by a materialized view.

    Only column indexes are represented. This keeps concurrent-refresh
    eligibility explicit: PostgreSQL requires a unique index without a
    predicate or expressions that includes every row in the view.
    """

    name: str
    columns: tuple[str, ...]
    unique: bool = False

    def __post_init__(self) -> None:
        _require_identifier(self.name, field_name="index name")
        if not self.columns:
            raise ValueError("index columns must not be empty")
        for column in self.columns:
            _require_identifier(column, field_name="index column")
        _require_distinct(self.columns, field_name="index columns")


@runtime_checkable
class MaterializedSelectable(Protocol):
    """Definition consumed by materialized-view lifecycle helpers."""

    @property
    def target(self) -> MaterializedViewTarget: ...

    @property
    def selectable(self) -> SelectBase: ...

    @property
    def logical_identity(self) -> tuple[str, ...]: ...

    @property
    def dependencies(self) -> tuple[MaterializedViewTarget, ...]: ...

    @property
    def indexes(self) -> tuple[MaterializedViewIndex, ...]: ...


@dataclass(frozen=True, slots=True)
class MaterializedViewSpec:
    """Immutable materialized-view definition with executable row identity.

    Dependencies are metadata for an owning registry or deployment tool. The
    lifecycle helpers deliberately operate on one view at a time and do not
    infer orchestration from them.
    """

    target: MaterializedViewTarget
    selectable: SelectBase
    logical_identity: tuple[str, ...]
    dependencies: tuple[MaterializedViewTarget, ...] = ()
    indexes: tuple[MaterializedViewIndex, ...] = ()

    def __post_init__(self) -> None:
        if not self.logical_identity:
            raise ValueError("logical_identity must not be empty")
        for column in self.logical_identity:
            _require_identifier(column, field_name="logical identity column")
        _require_distinct(
            self.logical_identity,
            field_name="logical_identity",
        )

        output_columns = set(self.selectable.selected_columns.keys())
        unknown_identity = sorted(set(self.logical_identity) - output_columns)
        if unknown_identity:
            raise ValueError(
                f"logical_identity columns are not selected: {unknown_identity}"
            )

        index_names = tuple(index.name for index in self.indexes)
        _require_distinct(index_names, field_name="index names")
        for index in self.indexes:
            unknown_index_columns = sorted(set(index.columns) - output_columns)
            if unknown_index_columns:
                raise ValueError(
                    f"index {index.name!r} columns are not selected: "
                    f"{unknown_index_columns}"
                )

        if self.target in self.dependencies:
            raise ValueError("a materialized view cannot depend on itself")
        if len(self.dependencies) != len(set(self.dependencies)):
            raise ValueError("dependencies must not contain duplicates")

    @property
    def concurrent_refresh_indexes(self) -> tuple[MaterializedViewIndex, ...]:
        """Declared indexes whose shape can support concurrent refresh."""
        return tuple(index for index in self.indexes if index.unique)
