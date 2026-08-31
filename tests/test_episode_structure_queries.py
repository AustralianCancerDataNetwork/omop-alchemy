"""Canonical episode and hierarchy projections."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite

from omop_alchemy.cdm.model.structural import Episode
from omop_alchemy.toolkit.episodes.derivation import (
    CANONICAL_EPISODE_COLUMNS,
    CANONICAL_EPISODE_OPTIONAL_COLUMNS,
    canonical_episode_projection,
    direct_episode_relationship_projection,
    episode_descendants,
    episode_event_hierarchy_projection,
)


def test_canonical_episode_projection_has_stable_fields():
    statement = canonical_episode_projection()

    assert tuple(statement.selected_columns.keys()) == tuple(
        map(str, CANONICAL_EPISODE_COLUMNS)
    )


def test_canonical_episode_projection_can_include_the_episode_concept_name(session):
    source = session.get(Episode, 100)
    assert source is not None
    session.add(
        Episode(
            episode_id=102,
            person_id=source.person_id,
            episode_start_date=date(2020, 2, 1),
            episode_end_date=None,
            episode_concept_id=0,
            episode_object_concept_id=source.episode_object_concept_id,
            episode_type_concept_id=source.episode_type_concept_id,
        )
    )
    session.flush()

    unlabelled = canonical_episode_projection().where(
        sa.column("episode_id").in_((100, 102))
    )
    labelled = canonical_episode_projection(include_concept_label=True).where(
        sa.column("episode_id").in_((100, 102))
    )
    unlabelled_rows = session.execute(unlabelled).mappings().all()
    labelled_rows = session.execute(labelled).mappings().all()

    assert tuple(labelled.selected_columns.keys()) == tuple(
        map(str, CANONICAL_EPISODE_COLUMNS + CANONICAL_EPISODE_OPTIONAL_COLUMNS)
    )
    assert (
        {row["episode_id"] for row in labelled_rows}
        == {row["episode_id"] for row in unlabelled_rows}
        == {100, 102}
    )
    labels = {row["episode_id"]: row["episode_concept_name"] for row in labelled_rows}
    assert labels == {100: "Disease Episode", 102: None}


def test_episode_concept_label_projection_compiles_on_supported_dialects():
    for dialect in (sqlite.dialect(), postgresql.dialect()):
        compiled = str(
            canonical_episode_projection(include_concept_label=True).compile(
                dialect=dialect
            )
        )
        assert "LEFT OUTER JOIN" in compiled


def test_direct_episode_relationships_preserve_person_and_depth(session):
    rows = (
        session.execute(
            direct_episode_relationship_projection().where(
                sa.column("root_episode_id") == 100
            )
        )
        .mappings()
        .all()
    )

    assert rows == [
        {
            "root_episode_id": 100,
            "episode_id": 101,
            "episode_parent_id": 100,
            "person_id": 1,
            "depth": 1,
        }
    ]


def test_recursive_episode_projection_includes_root_and_descendants(session):
    hierarchy = episode_descendants(root_episode_id=100)
    rows = session.execute(
        sa.select(hierarchy.c.episode_id, hierarchy.c.depth).order_by(
            hierarchy.c.depth,
            hierarchy.c.episode_id,
        )
    ).all()

    assert rows == [(100, 0), (101, 1)]


def test_recursive_episode_projection_can_exclude_root(session):
    hierarchy = episode_descendants(root_episode_id=100, include_root=False)

    assert session.execute(
        sa.select(hierarchy.c.episode_id, hierarchy.c.depth)
    ).all() == [(101, 1)]


def test_episode_event_projection_carries_owning_depth(session):
    rows = (
        session.execute(episode_event_hierarchy_projection(root_episode_id=100))
        .mappings()
        .all()
    )

    assert rows == [
        {
            "root_episode_id": 100,
            "linked_episode_id": 101,
            "person_id": 1,
            "episode_depth": 1,
            "event_id": 1,
            "event_field_concept_id": 1147127,
        }
    ]


def test_recursive_episode_projection_compiles_on_supported_dialects():
    hierarchy = episode_descendants(root_episode_id=100)
    statement = sa.select(*hierarchy.c)

    for dialect in (sqlite.dialect(), postgresql.dialect()):
        compiled = str(statement.compile(dialect=dialect))
        assert "WITH RECURSIVE" in compiled
        assert "episode_parent_id" in compiled
