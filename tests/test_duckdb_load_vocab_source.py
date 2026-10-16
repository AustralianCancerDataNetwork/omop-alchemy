"""DuckDB end-to-end coverage for load_vocab_source.

DuckDB enforces relationship.relationship_concept_id's FK to concept 
(and concept.domain_id/vocabulary_id/concept_class_id's FKs to
their own reference tables) at insert time. REQUIRED_VOCAB_MODELS and
ATHENA_LOAD_ORDER must load Concept before Relationship for this to succeed
on DuckDB.

load_vocab_source must validate DuckDB's deferred FKs
(domain/vocabulary/concept_class/relationship's own use_alter=True FKs,
never enforced at the DB level) once the whole load completes, raising on
an orphaned row rather than leaving it unnoticed.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from oa_configurator import Dialect, ResolvedCDMDatabase
from oa_configurator.testing import IsolatedTestDatabase, isolated_test_database
from orm_loader.backends import staging_schema_claim
from orm_loader.backends.base import DeferredForeignKeyViolationError

from omop_alchemy.config import OmopAlchemyConfig
from omop_alchemy.maintenance._cli_utils import Status
from omop_alchemy.maintenance.cli_vocab import REQUIRED_VOCAB_MODELS, load_vocab_source
from omop_alchemy.maintenance.context import MaintenanceContext

from .conftest import _ATHENA_FIXTURE_DATA, _write_fixture_csv


@pytest.fixture
def staged_duckdb_db(request) -> Iterator[IsolatedTestDatabase]:
    """Fresh DuckDB database with orm-loader's staging schema claimed.
    """
    with isolated_test_database(
        OmopAlchemyConfig,
        "test_cdm_db_duckdb",
        dialect=Dialect.DUCKDB,
        request=request,
        schema_claims=[staging_schema_claim()],
    ) as db:
        yield db


@pytest.fixture
def staged_duckdb_engine(staged_duckdb_db: IsolatedTestDatabase):
    return staged_duckdb_db.connection.engine


@pytest.fixture
def staged_duckdb_resolved(staged_duckdb_db: IsolatedTestDatabase) -> ResolvedCDMDatabase:
    assert isinstance(staged_duckdb_db.resolved, ResolvedCDMDatabase)
    return staged_duckdb_db.resolved


def _fixtures_with_sentinel_concept() -> dict[str, dict[str, tuple[Any, ...]]]:
    """Athena fixture data plus concept_id=0, the sentinel every other
    fixture row's domain_concept_id/concept_class_concept_id/
    relationship_concept_id points at.

    domain_id="Metadata", vocabulary_id="CDM", and concept_class_id="Field"
    are reused from the existing fixture rows rather than OMOP's real
    "None"/"Undefined" placeholders, so this sentinel row doesn't need its
    own new domain/vocabulary/concept_class rows to satisfy its own FKs.
    """
    fixtures = {k: dict(v) for k, v in _ATHENA_FIXTURE_DATA.items()}
    concept = fixtures["concept"]
    fixtures["concept"] = {
        "concept_id": (0,) + concept["concept_id"],
        "concept_name": ("No matching concept",) + concept["concept_name"],
        "domain_id": ("Metadata",) + concept["domain_id"],
        "vocabulary_id": ("CDM",) + concept["vocabulary_id"],
        "concept_class_id": ("Field",) + concept["concept_class_id"],
        "standard_concept": (None,) + concept["standard_concept"],
        "concept_code": ("OMOP generated",) + concept["concept_code"],
        "valid_start_date": ("19700101",) + concept["valid_start_date"],
        "valid_end_date": ("20991231",) + concept["valid_end_date"],
        "invalid_reason": (None,) + concept["invalid_reason"],
    }
    return fixtures


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_load_vocab_source_end_to_end_on_duckdb(staged_duckdb_engine, staged_duckdb_resolved, tmp_path: Path):
    source = tmp_path / "athena_source"
    source.mkdir()
    for table_name, data in _fixtures_with_sentinel_concept().items():
        _write_fixture_csv(source, table_name, data)

    context = MaintenanceContext(
        resolved=staged_duckdb_resolved, engine=staged_duckdb_engine, vocab_engine=staged_duckdb_engine
    )
    report = load_vocab_source(context, source_path=source)

    required_names = {m.__tablename__ for m in REQUIRED_VOCAB_MODELS}
    loaded_required = {
        result.table_name: result for result in report.results if result.table_name in required_names
    }
    assert required_names == set(loaded_required)
    assert all(result.status == Status.LOADED for result in loaded_required.values())
    assert loaded_required["concept"].row_count == 8
    assert loaded_required["relationship"].row_count == 2


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_load_vocab_source_raises_on_orphaned_deferred_fk_on_duckdb(
    staged_duckdb_engine, staged_duckdb_resolved, tmp_path: Path
):
    source = tmp_path / "athena_source"
    source.mkdir()
    fixtures = _fixtures_with_sentinel_concept()
    domain = fixtures["domain"]
    fixtures["domain"] = {**domain, "domain_concept_id": (999_999,) + domain["domain_concept_id"][1:]}
    for table_name, data in fixtures.items():
        _write_fixture_csv(source, table_name, data)

    context = MaintenanceContext(
        resolved=staged_duckdb_resolved, engine=staged_duckdb_engine, vocab_engine=staged_duckdb_engine
    )
    with pytest.raises(DeferredForeignKeyViolationError):
        load_vocab_source(context, source_path=source)
