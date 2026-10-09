from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Iterable

import sqlalchemy as sa

if TYPE_CHECKING:
    from .context import MaintenanceContext


class TableCategory(StrEnum):
    """An OMOP CDM table's structural category, carrying its render style.

    Represents a logical grouping of tables as defined by the 
    [OMOP CDM spec](https://ohdsi.github.io/CommonDataModel/). 
    The grouping is also reflected in the subpackage under 
    ``omop_alchemy.cdm.model``, where the same logical grouping is used
    to organize the ORM classes. 

    Notes
    -----
    This logical grouping is independent of the physical schema in
    which the table's data is stored (its own ``schema_tag``).

    Parameters
    ----------
    code : str
        The category's string value.
    style : str
        Rich color/style name used to render this category.
    """

    def __new__(cls, code: str, style: str):
        obj = str.__new__(cls, code)
        obj._value_ = code
        return obj

    def __init__(self, code: str, style: str):
        self.style = style

    CLINICAL = ("clinical", "bright_blue")
    DERIVED = ("derived", "blue")
    HEALTH_ECONOMIC = ("health_economic", "green")
    HEALTH_SYSTEM = ("health_system", "bright_cyan")
    METADATA = ("metadata", "white")
    STRUCTURAL = ("structural", "magenta")
    UNSTRUCTURED = ("unstructured", "bright_magenta")
    VOCABULARY = ("vocabulary", "yellow")


@dataclass(frozen=True)
class MaintenanceTable:
    table_name: str
    model_name: str
    model_module: str
    category: TableCategory
    table: sa.Table
    primary_key_columns: tuple[sa.Column[object], ...]

    @property
    def schema_tag(self) -> str:
        """The schema_translate_map key this table's data physically lives
        under, read off its own declared schema tag.

        Independent of TableCategory: category is a logical/folder grouping
        (e.g. cohort/cohort_definition are RESULTS-category despite
        classifying as "derived" in the CDM sense), schema_tag is where the
        table's rows physically live.
        """
        tag = self.table.schema
        if tag is None:
            raise TypeError(f"{self.table_name}: table has no schema tag.")
        return tag

    @property
    def is_vocabulary(self) -> bool:
        return self.category is TableCategory.VOCABULARY

    @property
    def has_single_primary_key(self) -> bool:
        return len(self.primary_key_columns) == 1

    @property
    def primary_key_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.primary_key_columns)

    @property
    def single_primary_key_name(self) -> str | None:
        if not self.has_single_primary_key:
            return None
        return self.primary_key_columns[0].name

    @property
    def has_single_integer_primary_key(self) -> bool:
        return (
            self.has_single_primary_key
            and isinstance(self.primary_key_columns[0].type, sa.Integer)
        )


def all_table_names(bindable: sa.Engine | sa.Connection, *, schema: str | None) -> set[str]:
    """Every table name visible in *schema*, ordinary and foreign alike.

    ``get_table_names`` omits foreign tables, so a vocabulary exposed
    through a federated wrapper would look absent without this. Foreign
    tables are a PostgreSQL concept, so the second lookup is skipped where
    the inspector does not offer it.
    """
    inspector = sa.inspect(bindable)
    names = set(inspector.get_table_names(schema=schema))
    foreign_table_names = getattr(inspector, "get_foreign_table_names", None)
    if foreign_table_names is not None:
        names |= set(foreign_table_names(schema=schema))
    return names


@dataclass(frozen=True)
class TableTarget:
    """Where one table's maintenance work has to run.

    Built only by ``MaintenanceContext.targets()``, so every command reads
    the same answer instead of re-deriving it.

    Attributes
    ----------
    table : MaintenanceTable
        The table this target describes.
    bind : sqlalchemy.Engine
        Engine hosting this table, already routed by schema tag.
    physical_schema : str or None
        Schema the tag resolves to on *bind*. None for a dialect with no
        real schema concept.
    foreign_keys_creatable : bool
        Whether this table's foreign keys to other tags can physically
        exist. False for a key crossing a database boundary, which lets
        reconciliation tell an impossible constraint from a broken one.
    """

    table: MaintenanceTable
    bind: sa.Engine
    physical_schema: str | None
    foreign_keys_creatable: bool

    @property
    def schema_tag(self) -> str:
        return self.table.schema_tag

    @property
    def table_name(self) -> str:
        return self.table.table_name

    def exists(self) -> bool:
        """Is this table physically present on its own bind?

        Unions ordinary and foreign tables, so a vocabulary exposed through
        a federated wrapper is not reported absent. Foreign tables are a
        PostgreSQL concept, so the lookup is skipped on a dialect whose
        inspector does not offer it.
        """
        if sa.inspect(self.bind).has_table(self.table_name, schema=self.physical_schema):
            return True
        return self.table_name in all_table_names(
            self.bind, schema=self.physical_schema
        )


def _mapped_cdm_table_classes() -> Iterable[type]:
    import omop_alchemy.cdm.model  # noqa: F401
    from orm_loader.helpers import Base

    return [
        mapper.class_
        for mapper in Base.registry.mappers
        if getattr(mapper.class_, "__omop_is_cdm_table__", False)
    ]


def _table_category(mapped_class: type) -> TableCategory:
    category_name = getattr(mapped_class, "__omop_table_category__", None)
    if category_name is None:
        raise RuntimeError(
            f"{mapped_class.__name__} is missing __omop_table_category__"
        )
    return TableCategory(category_name)


def collect_maintenance_tables() -> list[MaintenanceTable]:
    tables: list[MaintenanceTable] = []

    for mapped_class in sorted(
        _mapped_cdm_table_classes(),
        key=lambda cls: cls.__table__.name,  # ty: ignore[unresolved-attribute]
    ):
        table = mapped_class.__table__  # ty: ignore[unresolved-attribute]
        tables.append(
            MaintenanceTable(
                table_name=table.name,
                model_name=mapped_class.__name__,
                model_module=mapped_class.__module__,
                category=_table_category(mapped_class),
                table=table,
                primary_key_columns=tuple(table.primary_key.columns),
            )
        )

    return tables


def maintenance_table_map() -> dict[str, MaintenanceTable]:
    return {
        table.table_name: table
        for table in collect_maintenance_tables()
    }


def _unique_table_names(table_names: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for table_name in table_names:
        if table_name in seen:
            continue
        seen.add(table_name)
        unique.append(table_name)
    return unique


def select_maintenance_tables(
    *,
    categories: Iterable[TableCategory] | None = None,
    exclude_categories: Iterable[TableCategory] | None = None,
    require_single_integer_primary_key: bool = False,
) -> list[MaintenanceTable]:
    selected = collect_maintenance_tables()

    if categories is not None:
        allowed = set(categories)
        selected = [
            table for table in selected
            if table.category in allowed
        ]

    if exclude_categories is not None:
        excluded = set(exclude_categories)
        selected = [
            table for table in selected
            if table.category not in excluded
        ]

    if require_single_integer_primary_key:
        selected = [
            table for table in selected
            if table.has_single_integer_primary_key
        ]

    return selected


def resolve_maintenance_tables(
    *,
    table_names: Iterable[str] | None = None,
    scope: TableCategory | None = None,
    require_single_integer_primary_key: bool = False,
) -> list[MaintenanceTable]:
    if table_names is not None:
        selected_by_name = maintenance_table_map()
        ordered_names = _unique_table_names(table_names)
        unknown_names = sorted(
            {
                table_name
                for table_name in ordered_names
                if table_name not in selected_by_name
            }
        )
        if unknown_names:
            raise RuntimeError(
                "Unknown ORM-managed table(s): "
                + ", ".join(unknown_names)
            )

        selected = [
            selected_by_name[table_name]
            for table_name in ordered_names
        ]
        if require_single_integer_primary_key:
            selected = [
                table
                for table in selected
                if table.has_single_integer_primary_key
            ]
        return selected

    if scope is not None:
        return select_maintenance_tables(
            categories=(scope,),
            require_single_integer_primary_key=require_single_integer_primary_key,
        )

    return select_maintenance_tables(
        require_single_integer_primary_key=require_single_integer_primary_key,
    )


def select_omop_tables(
    *,
    vocabulary_included: bool,
    require_single_integer_primary_key: bool = False,
) -> list[MaintenanceTable]:
    excluded_categories: tuple[TableCategory, ...] = ()
    if not vocabulary_included:
        excluded_categories = (TableCategory.VOCABULARY,)

    return select_maintenance_tables(
        exclude_categories=excluded_categories,
        require_single_integer_primary_key=require_single_integer_primary_key,
    )


def existing_maintenance_targets(
    context: MaintenanceContext,
    *,
    vocabulary_included: bool,
    categories: Iterable[TableCategory] | None = None,
    require_single_integer_primary_key: bool = False,
) -> list[TableTarget]:
    """Targets for ORM-managed tables that already exist on their own engine.

    Parameters
    ----------
    context : MaintenanceContext
    vocabulary_included : bool
        Include vocabulary tables. Ignored when *categories* is given.
    categories : Iterable[TableCategory], optional
        When given, selects tables by category via
        :func:`select_maintenance_tables` instead of *vocabulary_included*.
    require_single_integer_primary_key : bool, optional
    """
    selected = (
        select_maintenance_tables(
            categories=categories, require_single_integer_primary_key=require_single_integer_primary_key
        )
        if categories is not None
        else select_omop_tables(
            vocabulary_included=vocabulary_included,
            require_single_integer_primary_key=require_single_integer_primary_key,
        )
    )
    return [target for target in context.targets(selected) if target.exists()]


def missing_maintenance_targets(
    context: MaintenanceContext,
    *,
    vocabulary_included: bool,
) -> list[TableTarget]:
    """Targets for ORM-managed tables absent from their own engine."""
    return [
        target
        for target in context.targets(select_omop_tables(vocabulary_included=vocabulary_included))
        if not target.exists()
    ]
