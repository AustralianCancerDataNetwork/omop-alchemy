import pytest
import sqlalchemy as sa
from oa_configurator import Role
from oa_configurator.testing import scoped_test_schema
from omop_alchemy.maintenance.cli_schema import create_missing_tables

pytestmark = [pytest.mark.postgresql, pytest.mark.db_dialect]

def test_inspect_functional_index(pg_db):
    with scoped_test_schema(pg_db.resolved, prefix="funcidx_probe") as scoped:
        create_missing_tables(
            scoped.engine, vocab_engine=scoped.engine, vocabulary_included=True, resolved=scoped.resolved
        )
        with scoped.engine.connect() as conn:
            inspector = sa.inspect(conn)
            for idx in inspector.get_indexes("concept", schema=scoped.schemas[Role.PRIMARY]):
                if idx["name"] == "ix_concept_concept_name_lower":
                    print("FULL DICT:", idx)
                    for k, v in idx.items():
                        print(f"  {k!r}: {v!r}")
