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
from typing import Any, Literal

import pytest
import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Dialect, ResolvedCDMDatabase
from oa_configurator.testing import IsolatedTestDatabase, isolated_test_database
from orm_loader.backends import staging_schema_claim
from orm_loader.backends.base import DeferredForeignKeyViolationError

from omop_alchemy.cdm.model.vocabulary import Domain
from omop_alchemy.config import OmopAlchemyConfig
from omop_alchemy.maintenance._cli_utils import Status
from omop_alchemy.maintenance.cli_vocab import (
    REQUIRED_VOCAB_MODELS,
    VocabularyLoadError,
    _create_missing_vocabulary_tables,
    load_vocab_source,
)
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
def test_load_vocab_source_repeat_replace_load_on_duckdb(
    staged_duckdb_engine, staged_duckdb_resolved, tmp_path: Path
):
    source = tmp_path / "athena_source"
    source.mkdir()
    for table_name, data in _fixtures_with_sentinel_concept().items():
        _write_fixture_csv(source, table_name, data)

    context = MaintenanceContext(
        resolved=staged_duckdb_resolved, engine=staged_duckdb_engine, vocab_engine=staged_duckdb_engine
    )
    load_vocab_source(context, source_path=source)

    # Not blocked: domain/vocabulary/concept_class's own FK into concept is
    # deferred (cyclic) -> no real DuckDB constraint.
    report = load_vocab_source(
        context, source_path=source, tables=["domain", "vocabulary", "concept_class"]
    )
    assert all(result.status == Status.LOADED for result in report.results)

    # Genuinely blocked: concept's own FK columns are enforced, and other
    # tables' rows (e.g. domain, already loaded above) still reference it.
    with pytest.raises(ValueError, match="concept"):
        load_vocab_source(context, source_path=source, tables=["concept"])


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


@pytest.mark.duckdb
@pytest.mark.db_dialect
def test_load_vocab_source_standalone_cyclic_table_load_rolls_back_on_duckdb(
    staged_duckdb_engine, staged_duckdb_resolved, tmp_path: Path
):
    """Loading one cyclic table (domain) standalone must roll back an
    orphaned deferred FK.
    """
    context = MaintenanceContext(
        resolved=staged_duckdb_resolved, engine=staged_duckdb_engine, vocab_engine=staged_duckdb_engine
    )
    _create_missing_vocabulary_tables(context)

    source = tmp_path / "athena_source"
    source.mkdir()
    domain = _ATHENA_FIXTURE_DATA["domain"]
    orphaned_domain = {**domain, "domain_concept_id": (999_999,) + domain["domain_concept_id"][1:]}
    csv_path = _write_fixture_csv(source, "domain", orphaned_domain)

    with so.Session(staged_duckdb_engine) as session, pytest.raises(DeferredForeignKeyViolationError):
        Domain.load_csv(session, csv_path)

    with so.Session(staged_duckdb_engine) as verify_session:
        assert verify_session.execute(sa.select(Domain)).scalars().all() == []


@pytest.mark.duckdb
@pytest.mark.db_dialect
@pytest.mark.parametrize("merge_strategy", ["replace", "upsert"])
def test_load_vocab_source_single_cyclic_table_selection_rolls_back_on_duckdb(
    staged_duckdb_engine, staged_duckdb_resolved, tmp_path: Path, merge_strategy: Literal["replace", "upsert"]
):
    """`load-vocab-source --table domain` (selecting one cyclic table on its
    own, through load_vocab_source itself rather than load_csv directly)
    must roll back an orphaned deferred FK too: concept isn't part of this
    call's selection, so there's nothing to gain from load_vocab_source's
    usual after-the-whole-batch validation, and it must check inline instead.
    """
    source = tmp_path / "athena_source"
    source.mkdir()
    domain = _ATHENA_FIXTURE_DATA["domain"]
    orphaned_domain = {**domain, "domain_concept_id": (999_999,) + domain["domain_concept_id"][1:]}
    _write_fixture_csv(source, "domain", orphaned_domain)

    context = MaintenanceContext(
        resolved=staged_duckdb_resolved, engine=staged_duckdb_engine, vocab_engine=staged_duckdb_engine
    )
    with pytest.raises(VocabularyLoadError, match="domain"):
        load_vocab_source(
            context, source_path=source, tables=["domain"], merge_strategy=merge_strategy
        )

    with so.Session(staged_duckdb_engine) as verify_session:
        assert verify_session.execute(sa.select(Domain)).scalars().all() == []
