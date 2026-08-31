"""Domain-neutral projections over episode hierarchies and linked events."""

from __future__ import annotations

from typing import Any, cast

import sqlalchemy as sa
import sqlalchemy.orm as so
from sqlalchemy.sql.selectable import FromClause, SelectBase

from omop_alchemy.cdm.model.structural import Episode, Episode_Event
from omop_alchemy.cdm.model.vocabulary import Concept

from .contracts import CANONICAL_EPISODE_COLUMNS, EpisodeColumn


EpisodeSource = type[Episode] | FromClause | SelectBase
EpisodeEventSource = type[Episode_Event] | FromClause | SelectBase
# Hierarchy builders deliberately accept both mapped tables and pre-shaped
# selectables. This keeps filtering/aliasing at the caller boundary instead of
# forcing recursive queries to rediscover or override that source definition.


def _as_from_clause(
    source: FromClause | SelectBase,
    *,
    name: str,
) -> FromClause:
    # Recursive joins and column lookup need a FromClause. Wrapping a Select
    # once also gives it a stable name for readable SQL and repeated aliases.
    if isinstance(source, SelectBase):
        return source.subquery(name)
    if isinstance(source, FromClause):
        return source
    raise TypeError(f"{name} must be a SQLAlchemy Select or FromClause")


def _episode_source(source: EpisodeSource, *, name: str) -> FromClause:
    # Mapped classes contribute only their table here; relationship-bearing ORM
    # behavior is intentionally kept out of these SQL-only hierarchy builders.
    if isinstance(source, type):
        return cast(FromClause, getattr(source, "__table__"))
    return _as_from_clause(source, name=name)


def _episode_event_source(
    source: EpisodeEventSource,
    *,
    name: str,
) -> FromClause:
    # Episode_Event is normalized separately because callers may supply a
    # filtered link source while the hierarchy itself remains episode-relative.
    if isinstance(source, type):
        return cast(FromClause, getattr(source, "__table__"))
    return _as_from_clause(source, name=name)


def canonical_episode_projection(
    episode_model: EpisodeSource = Episode,
    *,
    include_concept_label: bool = False,
) -> sa.Select[Any]:
    """Project stable episode fields, optionally including its concept name."""
    episodes = _episode_source(episode_model, name="episode_projection")
    # Every output label comes from the shared contract so projected episodes
    # can be consumed by attachments and hierarchy helpers without table-specific
    # column knowledge.
    columns = [
        *(
            episodes.c[str(column)].label(str(column))
            for column in CANONICAL_EPISODE_COLUMNS
        )
    ]
    if not include_concept_label:
        return sa.select(*columns).select_from(episodes)

    # Keep the episode row when vocabulary data is absent; labels are an
    # optional presentation enrichment, not a filter on structural episodes.
    episode_concept = so.aliased(Concept, name="episode_projection_concept")
    columns.append(
        episode_concept.concept_name.label(str(EpisodeColumn.episode_concept_name))
    )
    return (
        sa.select(*columns)
        .select_from(episodes)
        .join(
            episode_concept,
            episode_concept.concept_id
            == episodes.c[str(EpisodeColumn.episode_concept_id)],
            isouter=True,
        )
    )


def direct_episode_relationship_projection(
    episode_model: EpisodeSource = Episode,
) -> sa.Select[Any]:
    """Select direct parent-child pairs with a depth of one."""
    episodes = _episode_source(episode_model, name="episode_relationships")
    # Episode IDs are joined within person so a malformed or imported dataset
    # cannot connect two patients merely because their IDs collide.
    parent = episodes.alias("parent_episode")
    child = episodes.alias("child_episode")
    return sa.select(
        parent.c[str(EpisodeColumn.episode_id)].label("root_episode_id"),
        child.c[str(EpisodeColumn.episode_id)].label(str(EpisodeColumn.episode_id)),
        child.c[str(EpisodeColumn.episode_parent_id)].label(
            str(EpisodeColumn.episode_parent_id)
        ),
        child.c[str(EpisodeColumn.person_id)].label(str(EpisodeColumn.person_id)),
        sa.literal(1).label("depth"),
    ).select_from(
        parent.join(
            child,
            sa.and_(
                child.c[str(EpisodeColumn.episode_parent_id)]
                == parent.c[str(EpisodeColumn.episode_id)],
                child.c[str(EpisodeColumn.person_id)]
                == parent.c[str(EpisodeColumn.person_id)],
            ),
        )
    )


def episode_descendants(
    *,
    root_episode_id: int | sa.ColumnElement[Any] | None = None,
    episode_model: EpisodeSource = Episode,
    include_root: bool = True,
    max_depth: int = 100,
    name: str = "episode_descendants",
) -> sa.CTE:
    """Build a recursive root-to-descendant projection with bounded depth."""
    if max_depth < 0:
        raise ValueError("max_depth must be non-negative")

    episodes = _episode_source(episode_model, name=f"{name}_source")
    episode_id = str(EpisodeColumn.episode_id)
    episode_parent_id = str(EpisodeColumn.episode_parent_id)
    person_id = str(EpisodeColumn.person_id)
    # The seed establishes the requested root and depth zero; the recursive
    # branch walks only same-person parent links and is bounded for cyclic data.
    seed = sa.select(
        episodes.c[episode_id].label("root_episode_id"),
        episodes.c[episode_id].label(episode_id),
        episodes.c[episode_parent_id].label(episode_parent_id),
        episodes.c[person_id].label(person_id),
        sa.literal(0).label("depth"),
    )
    if root_episode_id is not None:
        seed = seed.where(episodes.c[episode_id] == root_episode_id)

    hierarchy = seed.cte(f"{name}_walk", recursive=True)
    child = episodes.alias(f"{name}_child")
    hierarchy = hierarchy.union_all(
        sa.select(
            hierarchy.c.root_episode_id,
            child.c[episode_id],
            child.c[episode_parent_id],
            child.c[person_id],
            (hierarchy.c.depth + 1).label("depth"),
        )
        .select_from(
            hierarchy.join(
                child,
                sa.and_(
                    child.c[episode_parent_id] == hierarchy.c[episode_id],
                    child.c[person_id] == hierarchy.c[person_id],
                ),
            )
        )
        .where(hierarchy.c.depth < max_depth)
    )
    if include_root:
        return hierarchy
    # Preserve the same CTE shape while removing only the seed rows from the
    # public result, so downstream joins can use one stable hierarchy contract.
    return sa.select(*hierarchy.c).where(hierarchy.c.depth > 0).cte(name)


def episode_event_hierarchy_projection(
    *,
    root_episode_id: int | sa.ColumnElement[Any] | None = None,
    episode_model: EpisodeSource = Episode,
    episode_event_model: EpisodeEventSource = Episode_Event,
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
    event = _episode_event_source(
        episode_event_model,
        name="episode_event_hierarchy_events",
    )
    # Keep both the root and owning episode: child-linked events should remain
    # attributable to their direct owner while still supporting root-level
    # episode analyses.
    return sa.select(
        hierarchy.c.root_episode_id,
        hierarchy.c.episode_id.label("linked_episode_id"),
        hierarchy.c.person_id,
        hierarchy.c.depth.label("episode_depth"),
        event.c["event_id"],
        event.c["episode_event_field_concept_id"].label("event_field_concept_id"),
    ).select_from(hierarchy.join(event, event.c.episode_id == hierarchy.c.episode_id))
