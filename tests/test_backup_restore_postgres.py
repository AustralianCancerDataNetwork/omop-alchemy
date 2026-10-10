"""PostgreSQL backup and restore behavior for non-test target databases."""

from __future__ import annotations

import shutil
import subprocess
import uuid

import pytest
import sqlalchemy as sa
from oa_configurator import (
    CDMDatabaseConfig,
    ConnectionConfig,
    ResolvedCDMDatabase,
    Resolver,
    Role,
    SCHEMA_REGISTRY_SCHEMA,
    SchemaDriftError,
    StackConfig,
)
from oa_configurator.domains.resources.schema_registry import _record_schema_provenance
from oa_configurator.domains.resources.rectify import _refuse_production_collision
from oa_configurator.testing import resolve_with_role_schemas

from omop_alchemy.config import MAINTENANCE_SCHEMA, create_cdm_engines
from omop_alchemy.maintenance.cli_backup import (
    BackupFormat,
    create_database_backup,
    restore_database_backup,
)
from omop_alchemy.maintenance.context import MaintenanceContext


pytestmark = [pytest.mark.postgresql, pytest.mark.db_dialect]

def _unique_role_schemas(prefix: str = "roundtrip") -> dict[Role, str]:
    suffix = uuid.uuid4().hex[:8]
    return {
        Role.PRIMARY: f"{prefix}_cdm_{suffix}",
        Role.VOCAB: f"{prefix}_vocab_{suffix}",
        Role.RESULTS: f"{prefix}_results_{suffix}",
    }


def _create_target(pg_db, role_schemas):
    source_url = pg_db.connection.engine.url
    database_name = f"test_restore_{uuid.uuid4().hex[:10]}"
    config_name = pg_db.resolved.name
    target_url = source_url.set(database=database_name)
    _refuse_production_collision(target_url)
    admin = sa.create_engine(
        target_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
    finally:
        admin.dispose()

    target_connection = ConnectionConfig(
        dialect=target_url.drivername,
        host=target_url.host,
        port=target_url.port,
        user=target_url.username,
        password=target_url.password,
        database_name=database_name,
        test_only=False,
    )
    stack = StackConfig.for_session(
        connections={"target": target_connection},
        databases={
            config_name: CDMDatabaseConfig(
                connection="target",
                cdm_schema=role_schemas[Role.PRIMARY],
                vocab_schema=role_schemas[Role.VOCAB],
                results_schema=role_schemas[Role.RESULTS],
            )
        },
    )
    resolved = Resolver(stack).resolve_database(config_name)
    assert isinstance(resolved, ResolvedCDMDatabase)
    primary, vocab = create_cdm_engines(resolved, register_claims=False)
    context = MaintenanceContext(
        resolved=resolved,
        engine=primary,
        vocab_engine=vocab,
        resource_name=config_name,
    )
    return target_url, resolved, primary, vocab, context


def _drop_target(target_url):
    _refuse_production_collision(target_url)
    admin = sa.create_engine(
        target_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin.connect() as connection:
            connection.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": target_url.database},
            )
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{target_url.database}"')
    finally:
        admin.dispose()


def _source_context(pg_db, role_schemas):
    resolved = resolve_with_role_schemas(pg_db.resolved, role_schemas)
    assert isinstance(resolved, ResolvedCDMDatabase)
    primary, vocab = create_cdm_engines(resolved)
    return resolved, primary, vocab, MaintenanceContext(
        resolved=resolved, engine=primary, vocab_engine=vocab
    )


def _make_plain_backup(source_context, tmp_path):
    backup_path = tmp_path / "roundtrip.sql"
    create_database_backup(
        source_context,
        output_path=backup_path,
        backup_format=BackupFormat.PLAIN,
    )
    return backup_path


def _seed_round_trip_table(source_vocab, role_schemas):
    with source_vocab.begin() as connection:
        connection.exec_driver_sql(
            f'CREATE TABLE "{role_schemas[Role.VOCAB]}".round_trip (id integer)'
        )
        connection.exec_driver_sql(
            f'INSERT INTO "{role_schemas[Role.VOCAB]}".round_trip VALUES (17)'
        )


def _drop_source_schemas(source_primary, role_schemas):
    with source_primary.begin() as connection:
        for schema in role_schemas.values():
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def _old_plain_backup(source_engine, resolved, output_path):
    from omop_alchemy.backends import resolve_backend

    with source_engine.connect() as connection:
        schemas = sorted(resolved.occupied_schemas(connection))
    schemas = sorted({*schemas, MAINTENANCE_SCHEMA})
    tool_path, command, env, _ = resolve_backend(source_engine).prepare_backup(
        source_engine,
        str(output_path),
        BackupFormat.PLAIN.value,
        schemas=schemas,
    )
    subprocess.run(command, env=env, check=True, capture_output=True, text=True)
    assert tool_path


def test_current_plain_backup_restores_and_registers_non_test_target(pg_db, tmp_path):
    if not shutil.which("pg_dump") or not shutil.which("psql"):
        pytest.skip("plain PostgreSQL round trip requires pg_dump and psql")

    role_schemas = _unique_role_schemas()
    resolved, source_primary, source_vocab, source_context = _source_context(pg_db, role_schemas)
    target_url, target_resolved, target_primary, target_vocab, target_context = _create_target(
        pg_db, role_schemas
    )
    backup_path = tmp_path / "current.sql"
    try:
        _seed_round_trip_table(source_vocab, role_schemas)
        backup = create_database_backup(
            source_context,
            output_path=backup_path,
            backup_format=BackupFormat.PLAIN,
        )[0]
        assert SCHEMA_REGISTRY_SCHEMA in backup.schema_names
        assert MAINTENANCE_SCHEMA in backup.schema_names

        restore_database_backup(
            target_context,
            input_path=backup_path,
            backup_format=BackupFormat.PLAIN,
        )
        with target_vocab.connect() as connection:
            assert connection.execute(
                sa.text(f'SELECT id FROM "{role_schemas[Role.VOCAB]}".round_trip')
            ).scalar_one() == 17

        check_primary, check_vocab = create_cdm_engines(target_resolved)
        check_primary.dispose()
        if check_vocab is not check_primary:
            check_vocab.dispose()
    finally:
        _drop_source_schemas(source_primary, role_schemas)
        source_primary.dispose()
        if source_vocab is not source_primary:
            source_vocab.dispose()
        target_primary.dispose()
        if target_vocab is not target_primary:
            target_vocab.dispose()
        _drop_target(target_url)


def test_old_plain_backup_restores_data_then_reports_acknowledgment(pg_db, tmp_path):
    if not shutil.which("pg_dump") or not shutil.which("psql"):
        pytest.skip("plain PostgreSQL round trip requires pg_dump and psql")

    role_schemas = _unique_role_schemas()
    resolved, source_primary, source_vocab, source_context = _source_context(pg_db, role_schemas)
    target_url, _, target_primary, target_vocab, target_context = _create_target(
        pg_db, role_schemas
    )
    backup_path = tmp_path / "old.sql"
    try:
        _seed_round_trip_table(source_vocab, role_schemas)
        _old_plain_backup(source_primary, resolved, backup_path)
        with pytest.raises(RuntimeError, match="Restore complete, but this backup has no schema-registry baseline") as error:
            restore_database_backup(
                target_context,
                input_path=backup_path,
                backup_format=BackupFormat.PLAIN,
            )
        assert f"omop-config acknowledge-schema-migration --database {resolved.name}" in str(error.value)
        with target_vocab.connect() as connection:
            assert connection.execute(
                sa.text(f'SELECT id FROM "{role_schemas[Role.VOCAB]}".round_trip')
            ).scalar_one() == 17

        with target_primary.begin() as connection:
            for tag, schema in (
                (Role.PRIMARY.value, role_schemas[Role.PRIMARY]),
                (Role.VOCAB.value, role_schemas[Role.VOCAB]),
                (Role.RESULTS.value, role_schemas[Role.RESULTS]),
            ):
                _record_schema_provenance(
                    connection,
                    database_config_name=target_context.resolved.name,
                    schema_tag=tag,
                    new_physical_schema=schema,
                    reason=f"restored from {backup_path}",
                )
        registered_primary, registered_vocab = create_cdm_engines(target_context.resolved)
        registered_primary.dispose()
        if registered_vocab is not registered_primary:
            registered_vocab.dispose()
    finally:
        _drop_source_schemas(source_primary, role_schemas)
        source_primary.dispose()
        if source_vocab is not source_primary:
            source_vocab.dispose()
        target_primary.dispose()
        if target_vocab is not target_primary:
            target_vocab.dispose()
        _drop_target(target_url)


def test_restore_with_schema_drift_does_not_acknowledge(pg_db, tmp_path):
    if not shutil.which("pg_dump") or not shutil.which("psql"):
        pytest.skip("plain PostgreSQL round trip requires pg_dump and psql")

    role_schemas = _unique_role_schemas()
    resolved, source_primary, source_vocab, source_context = _source_context(pg_db, role_schemas)
    drifted_schemas = _unique_role_schemas(prefix="drifted")
    target_url, target_resolved, target_primary, target_vocab, target_context = _create_target(
        pg_db, drifted_schemas
    )
    backup_path = _make_plain_backup(source_context, tmp_path)
    try:
        with pytest.raises(SchemaDriftError, match="Schema drift detected"):
            restore_database_backup(
                target_context,
                input_path=backup_path,
                backup_format=BackupFormat.PLAIN,
            )
        with target_primary.connect() as connection:
            assert connection.execute(
                sa.text(
                    "SELECT physical_schema FROM oa_configurator_provenance.schema_registry "
                    "WHERE database_config_name = :name AND schema_tag = 'primary'"
                ),
                {"name": target_resolved.name},
            ).scalar_one() == role_schemas[Role.PRIMARY]
    finally:
        _drop_source_schemas(source_primary, role_schemas)
        source_primary.dispose()
        if source_vocab is not source_primary:
            source_vocab.dispose()
        target_primary.dispose()
        if target_vocab is not target_primary:
            target_vocab.dispose()
        _drop_target(target_url)


def test_plain_restore_refuses_existing_dumped_schema_before_psql(
    pg_db, tmp_path, monkeypatch
):
    if not shutil.which("pg_dump") or not shutil.which("psql"):
        pytest.skip("plain PostgreSQL round trip requires pg_dump and psql")

    role_schemas = _unique_role_schemas()
    resolved, source_primary, source_vocab, source_context = _source_context(pg_db, role_schemas)
    target_url, _, target_primary, target_vocab, target_context = _create_target(
        pg_db, role_schemas
    )
    backup_path = _make_plain_backup(source_context, tmp_path)
    try:
        with target_primary.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{role_schemas[Role.PRIMARY]}"')
        monkeypatch.setattr(
            "omop_alchemy.maintenance.cli_backup.subprocess.run",
            lambda *args, **kwargs: pytest.fail("psql ran before collision detection"),
        )
        with pytest.raises(RuntimeError, match="Plain-format restore needs an empty target"):
            restore_database_backup(
                target_context,
                input_path=backup_path,
                backup_format=BackupFormat.PLAIN,
            )
    finally:
        _drop_source_schemas(source_primary, role_schemas)
        source_primary.dispose()
        if source_vocab is not source_primary:
            source_vocab.dispose()
        target_primary.dispose()
        if target_vocab is not target_primary:
            target_vocab.dispose()
        _drop_target(target_url)
