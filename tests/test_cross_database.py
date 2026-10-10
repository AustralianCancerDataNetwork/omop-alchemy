"""filter_by_keys on a colocated deployment."""

from __future__ import annotations

import sqlalchemy as sa
import sqlalchemy.orm as so
import pytest
from oa_configurator import ConnectionConfig, ResolvedCDMDatabase
from sqlalchemy.exc import UnboundExecutionError

from omop_alchemy.config import create_cdm_engines
from omop_alchemy.cdm.model.clinical import Condition_Occurrence
from omop_alchemy.cdm.model.vocabulary import Concept
from omop_alchemy.cross_database import filter_by_keys
from omop_alchemy.cross_database import cdm_sessionmaker


def test_filter_by_keys_is_a_subquery_on_one_database(fresh_engine: sa.Engine):
    with so.Session(fresh_engine) as session:
        predicate = filter_by_keys(
            Condition_Occurrence.condition_concept_id,
            keys_select=sa.select(Concept.concept_id),
            session=session,
        )
    assert "SELECT" in str(predicate)


def test_colocated_session_binds_untagged_statements(fresh_engine, fresh_resolved):
    sessions = cdm_sessionmaker(
        fresh_resolved, primary=fresh_engine, vocab=fresh_engine
    )
    with sessions() as session:
        assert session.get_bind() is fresh_engine
        assert session.get_bind(clause=sa.text("SELECT 1")) is fresh_engine


def test_split_session_prefers_vocab_role_over_staging_tag(tmp_path):
    primary_connection = ConnectionConfig(
        dialect="sqlite", database_name=str(tmp_path / "primary.db")
    ).resolve("primary")
    vocab_connection = ConnectionConfig(
        dialect="sqlite", database_name=str(tmp_path / "vocab.db")
    ).resolve("vocab")
    resolved = ResolvedCDMDatabase(
        name="split",
        connection=primary_connection,
        schema_name=None,
        vocab_connection=vocab_connection,
        vocab_schema=None,
        results_schema=None,
    )
    primary, vocab = create_cdm_engines(resolved)
    try:
        sessions = cdm_sessionmaker(resolved, primary=primary, vocab=vocab)
        metadata = sa.MetaData()
        vocab_table = sa.Table("concept", metadata, sa.Column("id", sa.Integer), schema="vocab")
        primary_table = sa.Table("person", metadata, sa.Column("id", sa.Integer), schema="primary")
        staging_table = sa.Table("staging_concept", metadata, sa.Column("id", sa.Integer), schema="staging")
        with sessions() as session:
            assert session.get_bind(clause=sa.insert(vocab_table).values(id=1)) is vocab
            mixed = sa.insert(vocab_table).from_select(
                ["id"], sa.select(staging_table.c.id)
            )
            assert session.get_bind(clause=mixed) is vocab
            primary_with_custom_tag = sa.select(primary_table.c.id).select_from(
                primary_table.join(
                    staging_table, primary_table.c.id == staging_table.c.id
                )
            )
            assert session.get_bind(clause=primary_with_custom_tag) is primary
            with pytest.raises(UnboundExecutionError):
                session.get_bind(clause=sa.text("SELECT 1"))
            with pytest.raises(UnboundExecutionError):
                session.get_bind()
    finally:
        primary.dispose()
        vocab.dispose()
