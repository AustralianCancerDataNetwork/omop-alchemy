import sqlalchemy as sa

from omop_alchemy.maintenance.tables import (
    TableCategory,
    collect_maintenance_tables,
    resolve_maintenance_tables,
    select_maintenance_tables,
)


def test_collect_maintenance_tables_uses_decorator_metadata():
    """Test collect maintenance tables uses decorator metadata."""
    tables = {table.table_name: table for table in collect_maintenance_tables()}

    assert tables["person"].category is TableCategory.CLINICAL
    assert tables["concept"].category is TableCategory.VOCABULARY
    assert tables["person"].model_name == "Person"


def test_select_maintenance_tables_can_exclude_vocabulary():
    """Test select maintenance tables can exclude vocabulary."""
    tables = select_maintenance_tables(
        exclude_categories=(TableCategory.VOCABULARY,),
    )

    table_names = {table.table_name for table in tables}
    assert "person" in table_names
    assert "concept" not in table_names


def test_select_maintenance_tables_can_require_single_integer_primary_key():
    """Test select maintenance tables can require single integer primary key."""
    tables = select_maintenance_tables(require_single_integer_primary_key=True)
    table_names = {table.table_name for table in tables}

    assert "person" in table_names
    assert "concept_relationship" not in table_names
    assert all(isinstance(table.primary_key_columns[0].type, sa.Integer) for table in tables)


def test_resolve_maintenance_tables_deduplicates_repeated_names_preserving_order():
    """Test resolve maintenance tables deduplicates repeated names preserving order."""
    tables = resolve_maintenance_tables(
        table_names=("person", "concept", "person", "concept"),
    )

    assert [table.table_name for table in tables] == ["person", "concept"]
