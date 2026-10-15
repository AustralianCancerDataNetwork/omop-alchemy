import dataclasses

import pytest
import sqlalchemy as sa

from omop_alchemy.maintenance import cli_schema_info
from omop_alchemy.maintenance.cli_schema import _create_missing_tables
from omop_alchemy.maintenance.cli_schema_doctor import collect_doctor_report
from omop_alchemy.maintenance.context import MaintenanceContext


def test_doctor_uses_supplied_engine_without_resolving_config_or_disposing(
    fresh_engine,
    fresh_resolved,
    monkeypatch,
) -> None:
    """doctor takes an already-built context from its caller."""
    engine = fresh_engine
    resolved = dataclasses.replace(fresh_resolved, name="manual_cdm", schema_name="analytics")
    disposed_engines: list[sa.engine.Engine] = []
    inspected: dict[str, object] = {}
    original_dispose = sa.engine.Engine.dispose

    def collect_missing(context, *, vocabulary_included=True):
        inspected.update(
            engine=context.engine,
            vocabulary_included=vocabulary_included,
        )
        return []

    def track_dispose(self, *args, **kwargs):
        disposed_engines.append(self)
        return original_dispose(self, *args, **kwargs)

    monkeypatch.setattr(cli_schema_info, "collect_missing_tables", collect_missing)
    monkeypatch.setattr(sa.engine.Engine, "dispose", track_dispose)

    report = collect_doctor_report(
        MaintenanceContext(resolved=resolved, engine=engine, vocab_engine=engine, resource_name="manual_cdm"),
        vocabulary_included=False,
    )

    assert report.info.engine_url == str(engine.url)
    assert report.info.backend == "sqlite"
    assert report.info.db_schema == "analytics"
    assert report.info.resource_name == "manual_cdm"
    assert report.info.connection_ready is True
    assert inspected == {
        "engine": engine,
        "vocabulary_included": False,
    }
    assert engine not in disposed_engines


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_doctor_deep_runs_fk_validation_on_duckdb_despite_no_trigger_support(fresh_duckdb_engine, fresh_duckdb_resolved):
    context = MaintenanceContext(resolved=fresh_duckdb_resolved, engine=fresh_duckdb_engine, vocab_engine=fresh_duckdb_engine)
    _create_missing_tables(context, vocabulary_included=True)

    report = collect_doctor_report(context, vocabulary_included=True, deep=True)

    foreign_keys_check = next(c for c in report.checks if c.name == "foreign keys")
    validation_check = next(c for c in report.checks if c.name == "foreign key validation")

    assert foreign_keys_check.status == "skipped"
    assert validation_check.status == "passed"
    assert report.foreign_key_validation is not None
