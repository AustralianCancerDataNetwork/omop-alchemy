from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Hashable, Sequence
from typing import Protocol, TypeVar

RowT = TypeVar("RowT")
SummaryT = TypeVar("SummaryT")


class _SummaryFactory(Protocol[RowT, SummaryT]):
    def __call__(
        self,
        rows: Sequence[RowT],
        /,
        *,
        group_key: object,
    ) -> SummaryT: ...


def group_and_summarize(
    rows: Sequence[RowT],
    key: Callable[[RowT], Hashable],
    summarize: _SummaryFactory[RowT, SummaryT],
) -> list[SummaryT]:
    """Group ``rows`` by ``key`` and construct one summary per group.

    Groups retain first-seen key order. Analytics callers expose that order to
    users, so this helper deliberately relies on insertion-ordered mappings
    rather than sorting values whose key types may not be mutually comparable.
    """
    grouped: dict[Hashable, list[RowT]] = defaultdict(list)
    for row in rows:
        grouped[key(row)].append(row)
    return [
        summarize(group_rows, group_key=group_key)
        for group_key, group_rows in grouped.items()
    ]
