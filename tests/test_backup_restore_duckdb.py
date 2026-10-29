"""DuckDB backup and restore: in-process, schema-scoped Parquet round trip."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from oa_configurator import Role, physical_schema_of

from omop_alchemy.backends.resolve import resolve_backend
from omop_alchemy.cdm.model.vocabulary import Concept, Domain
from omop_alchemy.maintenance.cli_backup import (
    BackupFormat,
    create_database_backup,
    restore_database_backup,
)
from omop_alchemy.maintenance.cli_schema import _create_missing_tables
from omop_alchemy.maintenance.cli_vocab import load_vocab_source
from omop_alchemy.maintenance.context import MaintenanceContext

from .conftest import _ATHENA_FIXTURE_DATA, _write_fixture_csv
from .test_duckdb_load_vocab_source import (
    staged_duckdb_db,
    staged_duckdb_engine,
    staged_duckdb_resolved,
)

__all__ = ["staged_duckdb_db", "staged_duckdb_engine", "staged_duckdb_resolved"]


pytestmark = [pytest.mark.duckdb, pytest.mark.db_dialect]


def _seed(context: MaintenanceContext, tmp_path) -> None:
    source = tmp_path / "athena_source"
    source.mkdir()
    for table_name, data in _ATHENA_FIXTURE_DATA.items():
        _write_fixture_csv(source, table_name, data)
    load_vocab_source(context, source_path=source)


def test_backup_restore_round_trip_restores_data(staged_duckdb_engine, staged_duckdb_resolved, tmp_path):
    context = MaintenanceContext(
        resolved=staged_duckdb_resolved, engine=staged_duckdb_engine, vocab_engine=staged_duckdb_engine
    )
    _seed(context, tmp_path)

    with Session(staged_duckdb_engine) as session:
        domain_count = session.execute(sa.select(sa.func.count()).select_from(Domain)).scalar_one()
        concept_count = session.execute(sa.select(sa.func.count()).select_from(Concept)).scalar_one()
    assert domain_count > 0
    assert concept_count > 0

    backup_dir = tmp_path / "backup"
    results = create_database_backup(context, output_path=backup_dir, backup_format=BackupFormat.PARQUET)
    assert len(results) == 1
    assert results[0].backup_format is BackupFormat.PARQUET
    assert backup_dir.is_dir()
    assert any(backup_dir.glob("*__concept.parquet"))
    assert any(backup_dir.glob("*__domain.parquet"))

    primary_schema = physical_schema_of(staged_duckdb_engine, schema_tag=Role.PRIMARY.value)
    with staged_duckdb_engine.connect() as connection:
        table_names = sa.inspect(connection).get_table_names(schema=primary_schema)
        connection.commit()
        resolve_backend(staged_duckdb_engine).truncate_table_batch(
            connection,
            [(Role.PRIMARY.value, name) for name in table_names],
            restart_identities=False,
            cascade=False,
        )

    with Session(staged_duckdb_engine) as session:
        assert session.execute(sa.select(sa.func.count()).select_from(Domain)).scalar_one() == 0
        assert session.execute(sa.select(sa.func.count()).select_from(Concept)).scalar_one() == 0

    restore_results = restore_database_backup(context, input_path=backup_dir, backup_format=BackupFormat.PARQUET)
    assert len(restore_results) == 1
    assert restore_results[0].backup_format is BackupFormat.PARQUET

    with Session(staged_duckdb_engine) as session:
        assert session.execute(sa.select(sa.func.count()).select_from(Domain)).scalar_one() == domain_count
        assert session.execute(sa.select(sa.func.count()).select_from(Concept)).scalar_one() == concept_count


def test_backup_only_covers_occupied_schemas(fresh_duckdb_engine, fresh_duckdb_resolved, tmp_path):
    """A schema outside what this database entry claims must not appear in
    the backup, even though it lives in the same physical DuckDB file."""
    context = MaintenanceContext(
        resolved=fresh_duckdb_resolved, engine=fresh_duckdb_engine, vocab_engine=fresh_duckdb_engine
    )
    _create_missing_tables(context, vocabulary_included=True)

    with fresh_duckdb_engine.begin() as connection:
        connection.exec_driver_sql('CREATE SCHEMA "unrelated_other_db"')
        connection.exec_driver_sql('CREATE TABLE "unrelated_other_db".untouched (x INTEGER)')
        connection.exec_driver_sql('INSERT INTO "unrelated_other_db".untouched VALUES (1)')

    backup_dir = tmp_path / "backup"
    create_database_backup(context, output_path=backup_dir, backup_format=BackupFormat.PARQUET)

    assert not any(backup_dir.glob("unrelated_other_db__*"))

    with fresh_duckdb_engine.connect() as connection:
        assert sa.inspect(connection).get_table_names(schema="unrelated_other_db") == ["untouched"]
