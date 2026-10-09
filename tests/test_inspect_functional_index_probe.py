import pytest
import sqlalchemy as sa
from oa_configurator import Role
from oa_configurator.testing import scoped_test_schema
from omop_alchemy.maintenance.cli_schema import _create_missing_tables
from omop_alchemy.maintenance.context import MaintenanceContext

pytestmark = [pytest.mark.postgresql, pytest.mark.db_dialect]

def test_inspect_functional_index(pg_db):
    with scoped_test_schema(pg_db.resolved, prefix="funcidx_probe") as scoped:
        _create_missing_tables(
            MaintenanceContext(resolved=scoped.resolved, engine=scoped.engine, vocab_engine=scoped.engine),
            vocabulary_included=True,
        )
        with scoped.engine.connect() as conn:
            inspector = sa.inspect(conn)
            for idx in inspector.get_indexes("concept", schema=scoped.schemas[Role.PRIMARY]):
                if idx["name"] == "ix_concept_concept_name_lower":
                    print("FULL DICT:", idx)
                    for k, v in idx.items():
                        print(f"  {k!r}: {v!r}")
