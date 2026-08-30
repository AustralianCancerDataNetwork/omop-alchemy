"""PostgreSQL integration coverage for qualified materialized-view lifecycle."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from omop_alchemy.toolkit.core.materialization import (
    MaterializedViewIndex,
    MaterializedViewSpec,
    MaterializedViewTarget,
    create_materialized_view,
    create_materialized_view_indexes,
    drop_materialized_view,
    materialized_view_has_eligible_unique_index,
    refresh_materialized_view,
)


LEFT_SCHEMA = "mv_lifecycle_left"
RIGHT_SCHEMA = "mv_lifecycle_right"
VIEW_NAME = "shared name"


def _spec(schema: str, value: str) -> MaterializedViewSpec:
    return MaterializedViewSpec(
        target=MaterializedViewTarget(schema=schema, name=VIEW_NAME),
        selectable=sa.select(
            sa.literal(1).label("row_id"),
            sa.literal(value).label("payload"),
        ),
        logical_identity=("row_id",),
        indexes=(
            MaterializedViewIndex(
                name="shared_name_row_id_uq",
                columns=("row_id",),
                unique=True,
            ),
        ),
    )


def _view_exists(connection: sa.Connection, target: MaterializedViewTarget) -> bool:
    return bool(
        connection.execute(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_matviews
                    WHERE schemaname = :schema
                      AND matviewname = :name
                )
                """
            ),
            {"schema": target.schema, "name": target.name},
        ).scalar_one()
    )


@pytest.mark.requires_database("test_cdm_db")
def test_lifecycle_is_scoped_to_the_requested_schema(pg_engine):
    left = _spec(LEFT_SCHEMA, "left")
    right = _spec(RIGHT_SCHEMA, "right")

    try:
        with pg_engine.begin() as connection:
            connection.execute(
                sa.text(f'DROP SCHEMA IF EXISTS "{LEFT_SCHEMA}" CASCADE')
            )
            connection.execute(
                sa.text(f'DROP SCHEMA IF EXISTS "{RIGHT_SCHEMA}" CASCADE')
            )
            connection.execute(sa.text(f'CREATE SCHEMA "{LEFT_SCHEMA}"'))
            connection.execute(sa.text(f'CREATE SCHEMA "{RIGHT_SCHEMA}"'))

            create_materialized_view(connection, left)
            create_materialized_view(connection, right)
            create_materialized_view_indexes(connection, left)
            create_materialized_view_indexes(connection, right)

        with pg_engine.begin() as connection:
            assert materialized_view_has_eligible_unique_index(
                connection,
                left,
            )
            assert materialized_view_has_eligible_unique_index(
                connection,
                right,
            )

            refresh_materialized_view(connection, left, concurrently=True)
            drop_materialized_view(connection, left)

            assert not _view_exists(connection, left.target)
            assert _view_exists(connection, right.target)
    finally:
        with pg_engine.begin() as connection:
            connection.execute(
                sa.text(f'DROP SCHEMA IF EXISTS "{LEFT_SCHEMA}" CASCADE')
            )
            connection.execute(
                sa.text(f'DROP SCHEMA IF EXISTS "{RIGHT_SCHEMA}" CASCADE')
            )
