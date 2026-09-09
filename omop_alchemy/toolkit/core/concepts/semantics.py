"""Deferred references to governed ``omop-semantics`` units.

Domain packages declare concept groups as module-level constants. Resolving the
bundled semantics runtime while those modules import would make an optional
dependency mandatory and could make otherwise declarative imports perform
unwanted work. ``SemanticUnitRef`` stores only a stable path at construction and
resolves that path when a concept-group role is first read.

The reference exposes all three roles of the governed unit without interpreting
or reshaping them. Narrow clinical groupings therefore remain governed in
``omop-semantics`` rather than being assembled locally by consumers.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Protocol

from omop_alchemy.toolkit.core._semantics import default_semantics_runtime


class ConceptGroupAnchors(Protocol):
    """Role-aware anchors consumed by :class:`ConceptGroupSpec`."""

    @property
    def parent_ids(self) -> Collection[int]: ...

    @property
    def excluded_parent_ids(self) -> Collection[int]: ...

    @property
    def exact_ids(self) -> Collection[int]: ...


@dataclass(frozen=True, slots=True)
class SemanticUnitRef:
    """Lazy, immutable reference to one complete governed semantic unit."""

    value_set: str
    unit: str

    def __post_init__(self) -> None:
        if not self.value_set.strip() or not self.unit.strip():
            raise ValueError("semantic value-set and unit names must not be empty")

    def _semantic_unit(self) -> object:
        value_set = getattr(default_semantics_runtime(), self.value_set)
        return getattr(value_set, self.unit)

    def _role_ids(self, role: str) -> frozenset[int]:
        return frozenset(int(value) for value in getattr(self._semantic_unit(), role))

    @property
    def parent_ids(self) -> frozenset[int]:
        return self._role_ids("parent_ids")

    @property
    def excluded_parent_ids(self) -> frozenset[int]:
        return self._role_ids("excluded_parent_ids")

    @property
    def exact_ids(self) -> frozenset[int]:
        return self._role_ids("exact_ids")

    def __repr__(self) -> str:
        return f"<SemanticUnitRef {self.value_set}.{self.unit}>"
