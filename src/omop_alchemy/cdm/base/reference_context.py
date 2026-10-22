from __future__ import annotations
import sqlalchemy.orm as so
import sqlalchemy as sa
from typing import Any


class ReferenceContext:
    """Read-only reference relationships for inspection and analytics.

    Analytical contexts define these relationships outside the bare ETL table.
    Explicit joins resolve local references to single-column target identity,
    with viewonly=True and selectin loading for batched navigation. Assigning
    a related object does not mutate or populate the local reference column.
    """

    @classmethod
    def _reference_relationship(
        cls,
        *,
        target: str,
        local_fk: str,
        remote_pk: str | None = None,
        uselist: bool = False,
    ):
        """Join a local reference to its target's single mapped primary key.

        Target metadata is resolved only when the join is configured, preserving
        declaration/import order. The released ``remote_pk`` argument remains
        accepted, but must name the derived primary-key attribute.
        """
        return so.declared_attr(
            lambda cls_: so.relationship(
                target,
                primaryjoin=lambda: (
                    getattr(cls_, local_fk)
                    == cls._reference_primary_key(cls_, target, remote_pk)
                ),
                foreign_keys=lambda: getattr(cls_, local_fk),
                viewonly=True,
                lazy="selectin",
                uselist=uselist,
            )
        )

    @staticmethod
    def _reference_primary_key(
        model: type[Any], target: str, remote_pk: str | None
    ) -> so.InstrumentedAttribute[Any]:
        target_model = sa.inspect(model).registry._class_registry.get(target)
        if not isinstance(target_model, type):
            raise ValueError(f"reference target {target!r} is missing or ambiguous")
        mapper = sa.inspect(target_model)
        if len(mapper.primary_key) != 1:
            raise ValueError(
                f"reference target {target!r} must have exactly one mapped primary key"
            )
        attribute = mapper.get_property_by_column(mapper.primary_key[0]).class_attribute
        if remote_pk is not None and remote_pk != attribute.key:
            raise ValueError(
                f"reference target {target!r} primary key is {attribute.key!r}, "
                f"not {remote_pk!r}"
            )
        return attribute
