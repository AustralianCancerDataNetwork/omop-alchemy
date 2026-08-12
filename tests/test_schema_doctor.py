import sqlalchemy as sa

from omop_alchemy.maintenance import cli_schema_info
from omop_alchemy.maintenance.cli_schema_doctor import collect_doctor_report


def test_doctor_uses_borrowed_engine_without_resolving_config_or_disposing(
    monkeypatch,
) -> None:
    engine = sa.create_engine("sqlite://")
    disposed_engines: list[sa.engine.Engine] = []
    inspected: dict[str, object] = {}
    original_dispose = sa.engine.Engine.dispose

    def fail_config_resolution():
        raise AssertionError("doctor must not re-resolve configuration")

    def collect_missing(
        supplied_engine,
        *,
        db_schema=None,
        vocabulary_included=True,
    ):
        inspected.update(
            engine=supplied_engine,
            db_schema=db_schema,
            vocabulary_included=vocabulary_included,
        )
        return []

    def track_dispose(self, *args, **kwargs):
        disposed_engines.append(self)
        return original_dispose(self, *args, **kwargs)

    monkeypatch.setattr(cli_schema_info, "load_stack_config", fail_config_resolution)
    monkeypatch.setattr(cli_schema_info, "collect_missing_tables", collect_missing)
    monkeypatch.setattr(sa.engine.Engine, "dispose", track_dispose)

    report = collect_doctor_report(
        engine=engine,
        db_schema="analytics",
        resource_name="manual_cdm",
        vocabulary_included=False,
    )

    assert report.info.engine_url == "sqlite://"
    assert report.info.backend == "sqlite"
    assert report.info.db_schema == "analytics"
    assert report.info.resource_name == "manual_cdm"
    assert report.info.connection_ready is True
    assert inspected == {
        "engine": engine,
        "db_schema": "analytics",
        "vocabulary_included": False,
    }
    assert engine not in disposed_engines

    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT 1")) == 1

    engine.dispose()
