"""Domain-neutral projections over episode hierarchies and linked events."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from omop_alchemy.cdm.model.structural import Episode, Episode_Event

from .contracts import CANONICAL_EPISODE_COLUMNS


def canonical_episode_projection(
    episode_model: type[Episode] = Episode,
) -> sa.Select[Any]:
    """Project the stable fields needed to identify and interpret an episode."""
    return sa.select(
        *(
            getattr(episode_model, str(column)).label(str(column))
            for column in CANONICAL_EPISODE_COLUMNS
        )
    )


def direct_episode_relationship_projection(
    episode_model: type[Episode] = Episode,
) -> sa.Select[Any]:
    """Select direct parent-child pairs with a depth of one."""
    episodes = episode_model.__table__
    parent = episodes.alias("parent_episode")
    child = episodes.alias("child_episode")
    return sa.select(
        parent.c.episode_id.label("root_episode_id"),
        child.c.episode_id.label("episode_id"),
        child.c.episode_parent_id.label("episode_parent_id"),
        child.c.person_id.label("person_id"),
        sa.literal(1).label("depth"),
    ).select_from(
        parent.join(
            child,
            sa.and_(
                child.c.episode_parent_id == parent.c.episode_id,
                child.c.person_id == parent.c.person_id,
            ),
        )
    )


def episode_descendants(
    *,
    root_episode_id: int | sa.ColumnElement[Any] | None = None,
    episode_model: type[Episode] = Episode,
    include_root: bool = True,
    max_depth: int = 100,
    name: str = "episode_descendants",
) -> sa.CTE:
    """Build a recursive root-to-descendant projection with bounded depth."""
    if max_depth < 0:
        raise ValueError("max_depth must be non-negative")

    episodes = episode_model.__table__
    seed = sa.select(
        episodes.c.episode_id.label("root_episode_id"),
        episodes.c.episode_id.label("episode_id"),
        episodes.c.episode_parent_id.label("episode_parent_id"),
        episodes.c.person_id.label("person_id"),
        sa.literal(0).label("depth"),
    )
    if root_episode_id is not None:
        seed = seed.where(episodes.c.episode_id == root_episode_id)

    hierarchy = seed.cte(f"{name}_walk", recursive=True)
    child = episodes.alias(f"{name}_child")
    hierarchy = hierarchy.union_all(
        sa.select(
            hierarchy.c.root_episode_id,
            child.c.episode_id,
            child.c.episode_parent_id,
            child.c.person_id,
            (hierarchy.c.depth + 1).label("depth"),
        )
        .select_from(
            hierarchy.join(
                child,
                sa.and_(
                    child.c.episode_parent_id == hierarchy.c.episode_id,
                    child.c.person_id == hierarchy.c.person_id,
                ),
            )
        )
        .where(hierarchy.c.depth < max_depth)
    )
    if include_root:
        return hierarchy
    return sa.select(*hierarchy.c).where(hierarchy.c.depth > 0).cte(name)


def episode_event_hierarchy_projection(
    *,
    root_episode_id: int | sa.ColumnElement[Any] | None = None,
    episode_model: type[Episode] = Episode,
    episode_event_model: type[Episode_Event] = Episode_Event,
    include_root: bool = True,
    max_depth: int = 100,
) -> sa.Select[Any]:
    """Select linked events with their root episode, owning episode, and depth."""
    hierarchy = episode_descendants(
        root_episode_id=root_episode_id,
        episode_model=episode_model,
        include_root=include_root,
        max_depth=max_depth,
        name="episode_event_descendants",
    )
    event = episode_event_model.__table__
    return sa.select(
        hierarchy.c.root_episode_id,
        hierarchy.c.episode_id.label("linked_episode_id"),
        hierarchy.c.person_id,
        hierarchy.c.depth.label("episode_depth"),
        event.c.event_id,
        event.c.episode_event_field_concept_id.label("event_field_concept_id"),
    ).select_from(
        hierarchy.join(event, event.c.episode_id == hierarchy.c.episode_id)
    )
