"""Protect the package boundary for generic database lifecycle mechanics."""

from __future__ import annotations

import ast
from pathlib import Path

import omop_alchemy


FORBIDDEN_LIFECYCLE_DEFINITIONS = frozenset(
    {
        "CreateMaterializedView",
        "CreateMaterializedViewIndex",
        "DropMaterializedView",
        "MaterializationError",
        "MaterializedViewMixin",
        "MaterializedViewSpec",
        "RefreshMaterializedView",
        "create_materialized_view",
        "drop_materialized_view",
        "refresh_all_mvs",
        "refresh_materialized_view",
        "resolve_mv_refresh_order",
    }
)


def test_database_materialization_lifecycle_is_not_implemented_in_alchemy():
    """Keep generic lifecycle and DDL in orm-loader, its designated owner."""
    package_root = Path(omop_alchemy.__file__).parent
    definitions: dict[str, Path] = {}

    for module_path in package_root.rglob("*.py"):
        module = ast.parse(module_path.read_text(), filename=str(module_path))
        for node in ast.walk(module):
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name in FORBIDDEN_LIFECYCLE_DEFINITIONS:
                    definitions[node.name] = module_path.relative_to(package_root)

    assert definitions == {}, (
        "materialized-view database lifecycle belongs in "
        f"orm_loader.materialized_views, not omop_alchemy: {definitions}"
    )
