import pytest
import sqlalchemy as sa
from oa_configurator.testing import isolated_test_schema
from omop_alchemy.maintenance.cli_schema import create_missing_tables

pytestmark = [pytest.mark.postgresql, pytest.mark.db_dialect]

def test_inspect_functional_index(pg_db, pg_engine):
    with isolated_test_schema(pg_engine, prefix="funcidx_probe") as schema:
        import dataclasses
        resolved = dataclasses.replace(
            pg_db.resolved, schema_name=schema, vocab_schema=schema, results_schema=schema
        )
        engine = pg_engine.execution_options(
            schema_translate_map={"primary": schema, "vocab": schema, "results": schema}
        )
        create_missing_tables(engine, vocabulary_included=True, resolved=resolved)
        inspector = sa.inspect(engine)
        with engine.connect() as conn:
            inspector = sa.inspect(conn)
            for idx in inspector.get_indexes("concept", schema=schema):
                if idx["name"] == "ix_concept_concept_name_lower":
                    print("FULL DICT:", idx)
                    for k, v in idx.items():
                        print(f"  {k!r}: {v!r}")
