"""Shared internal construction for deterministic SQL window ranks."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import sqlalchemy as sa


def deterministic_row_number(
    *,
    partition_by: Iterable[sa.ColumnElement[Any]],
    order_by: Iterable[sa.ColumnElement[Any]],
    label: str,
) -> sa.ColumnElement[int]:
    """Build the row rank shared by temporal and observation selectors."""
    return (
        sa.func.row_number()
        .over(
            partition_by=tuple(partition_by),
            order_by=tuple(order_by),
        )
        .label(label)
    )
