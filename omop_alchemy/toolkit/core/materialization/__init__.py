"""PostgreSQL materialized-view definitions and lifecycle helpers.

This package owns the mechanics of targeting and operating on one materialized
view. Applications retain responsibility for dependency ordering, deployment
policy, and registry or command-line orchestration.
"""

from .contracts import (
    MaterializedSelectable,
    MaterializedViewIndex,
    MaterializedViewSpec,
    MaterializedViewTarget,
)
from .ddl import (
    CreateMaterializedView,
    CreateMaterializedViewIndex,
    DropMaterializedView,
    RefreshMaterializedView,
    render_materialized_view_target,
)
from .lifecycle import (
    ConcurrentRefreshNotEligibleError,
    MaterializationError,
    MaterializationFailure,
    MaterializationOperation,
    MaterializationOutcome,
    UnsupportedMaterializationDialectError,
    create_materialized_view,
    create_materialized_view_index,
    create_materialized_view_indexes,
    drop_materialized_view,
    materialized_view_has_eligible_unique_index,
    refresh_materialized_view,
)

__all__ = [
    "ConcurrentRefreshNotEligibleError",
    "CreateMaterializedView",
    "CreateMaterializedViewIndex",
    "DropMaterializedView",
    "MaterializationError",
    "MaterializationFailure",
    "MaterializationOperation",
    "MaterializationOutcome",
    "MaterializedSelectable",
    "MaterializedViewIndex",
    "MaterializedViewSpec",
    "MaterializedViewTarget",
    "RefreshMaterializedView",
    "UnsupportedMaterializationDialectError",
    "create_materialized_view",
    "create_materialized_view_index",
    "create_materialized_view_indexes",
    "drop_materialized_view",
    "materialized_view_has_eligible_unique_index",
    "refresh_materialized_view",
    "render_materialized_view_target",
]
