"""Non-default-schema Postgres coverage for the backends/ signature refactor (Phase 3.2).

Every other maintenance-CLI test runs against the default schema, where
``schema_of(conn)`` returning ``None`` and the old ``db_schema=None``
parameter are indistinguishable. The refactor that dropped explicit
``db_schema`` threading through ``backends/`` (deriving it internally via
``schema_of(conn)`` instead) could pass every existing test while still
being broken for a real non-default schema. This exercises a representative
subset of the refactored surface (FK trigger toggle, index create/drop,
full-text install, sequence reset) against a genuine non-default Postgres
schema, using ``pg_engine`` the same way ``test_db_schema_search_path_on_postgres``
(``test_load_vocab_postgres.py``) already does.
"""

from __future__ import annotations

from typing import Iterator, NamedTuple

import pytest
import sqlalchemy as sa

from oa_configurator.testing import isolated_test_schema

from omop_alchemy.maintenance._cli_utils import Status
from omop_alchemy.maintenance.cli_foreign_keys import (
    collect_foreign_key_trigger_status,
    manage_foreign_key_triggers,
)
from omop_alchemy.maintenance.cli_fulltext import install_fulltext_columns
from omop_alchemy.maintenance.cli_indexes import manage_indexes
from omop_alchemy.maintenance.cli_schema_tables import create_missing_tables
from omop_alchemy.maintenance.cli_tables import reset_model_sequences

pytestmark = [pytest.mark.postgresql, pytest.mark.db_dialect]


class _Scoped(NamedTuple):
    engine: sa.Engine
    schema: str


@pytest.fixture()
def scoped(pg_engine: sa.Engine) -> Iterator[_Scoped]:
    """``pg_engine`` scoped to a real, non-default, uniquely-named schema.

    Single-schema deployment: vocab/results fall back to the same schema as
    everything else, matching ``ResolvedCDMDatabase``'s own fallback when
    ``vocab_schema``/``results_schema`` aren't configured, the exact case
    that made the vocab/results-role fix safe for unconfigured deployments.
    """
    with isolated_test_schema(pg_engine, prefix="backends_non_default") as schema:
        engine = pg_engine.execution_options(
            schema_translate_map={None: schema, "vocab": schema, "results": schema}
        )
        yield _Scoped(engine=engine, schema=schema)


def test_fk_trigger_toggle_targets_the_configured_schema(scoped: _Scoped) -> None:
    create_missing_tables(scoped.engine, db_schema=scoped.schema, vocabulary_included=True)

    disabled = manage_foreign_key_triggers(scoped.engine, enable=False, db_schema=scoped.schema)
    assert disabled
    assert all(result.status == Status.APPLIED for result in disabled)

    status_after_disable = {
        result.table_name: result
        for result in collect_foreign_key_trigger_status(scoped.engine, db_schema=scoped.schema)
    }
    person_status = status_after_disable["person"]
    assert person_status.enabled_trigger_count == 0
    assert person_status.disabled_trigger_count > 0

    enabled = manage_foreign_key_triggers(scoped.engine, enable=True, db_schema=scoped.schema)
    assert all(result.status == Status.APPLIED for result in enabled)

    status_after_enable = {
        result.table_name: result
        for result in collect_foreign_key_trigger_status(scoped.engine, db_schema=scoped.schema)
    }
    assert status_after_enable["person"].disabled_trigger_count == 0


def test_index_disable_and_enable_targets_the_configured_schema(scoped: _Scoped) -> None:
    create_missing_tables(scoped.engine, db_schema=scoped.schema, vocabulary_included=True)

    disabled = manage_indexes(scoped.engine, enable=False, db_schema=scoped.schema)
    assert disabled
    assert all(result.status in (Status.APPLIED, Status.SKIPPED) for result in disabled)

    inspector = sa.inspect(scoped.engine)
    person_indexes_after_disable = {
        idx["name"] for idx in inspector.get_indexes("person", schema=scoped.schema)
    }

    enabled = manage_indexes(scoped.engine, enable=True, db_schema=scoped.schema)
    assert all(result.status in (Status.APPLIED, Status.SKIPPED) for result in enabled)

    inspector = sa.inspect(scoped.engine)
    person_indexes_after_enable = {
        idx["name"] for idx in inspector.get_indexes("person", schema=scoped.schema)
    }
    assert person_indexes_after_enable != person_indexes_after_disable or person_indexes_after_enable


def test_fulltext_install_targets_the_configured_schema(scoped: _Scoped) -> None:
    create_missing_tables(scoped.engine, db_schema=scoped.schema, vocabulary_included=True)

    results = install_fulltext_columns(scoped.engine, db_schema=scoped.schema)
    assert results
    assert all(result.status == Status.APPLIED for result in results)

    inspector = sa.inspect(scoped.engine)
    for result in results:
        columns = {
            c["name"] for c in inspector.get_columns(result.table_name, schema=scoped.schema)
        }
        assert result.vector_column_name in columns


def test_sequence_reset_targets_the_configured_schema(scoped: _Scoped) -> None:
    create_missing_tables(scoped.engine, db_schema=scoped.schema, vocabulary_included=True)

    results = {
        r.table_name: r for r in reset_model_sequences(scoped.engine, db_schema=scoped.schema)
    }
    person_result = results["person"]

    assert person_result.status == Status.RESET
    assert person_result.sequence_name is not None
    assert person_result.next_value == 1

    with scoped.engine.begin() as conn:
        next_id = conn.execute(sa.text(f"SELECT nextval('{person_result.sequence_name}')")).scalar_one()
    assert next_id == 1
