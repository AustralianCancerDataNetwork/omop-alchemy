# Quickstart

`OMOP_Alchemy` itself makes no assumptions about how PostgreSQL is provisioned: any
reachable instance works, local or otherwise. Docker orchestration for the OMOP stack
is handled at the workspace root (compose files there bring up every package's
containers as peers), not by a per-package `docker-compose.yaml` in this repo.

## Prerequisites

- A running PostgreSQL instance (any version supported by `omop_alchemy`'s SQLAlchemy dialects)
- `pip install omop-alchemy` (or an editable install from this repo)

## Configure

```bash
omop-config init
omop-config configure omop_alchemy
```

See [Configuration](configuration.md) for the full field reference.

---

## Running PostgreSQL tests locally

The test suite includes PostgreSQL-specific tests that skip automatically unless a `test_cdm_db_pg` database is configured in `~/.config/omop/config.toml`. They're resolved via oa-configurator's `isolated_test_database()`, wrapped in this repo's own `pg_db`/`pg_engine`/`pg_session` fixtures, and marked `@pytest.mark.postgresql` (plus `db_dialect` where a test could corrupt shared ORM metadata if run alongside SQLite in the same process). `addopts = "-m 'not db_dialect'"` excludes those by default, so a plain `pytest` run skips them with no manual filtering required. Run them explicitly with `pytest -m postgresql`.

!!! warning "This test database is destructive."
    `pg_session`-backed tests drop and recreate every non-system schema (not just `public`) both before and after each test. `test_cdm_db_pg` must point to a **dedicated, empty test database**, never to a database that contains real data. The test suite enforces this: it fails loudly if the configured database is not marked `test_only = true` in your config. The suite runs sequentially by design and does not support `pytest-xdist`: it fails loudly under `-n 2` or higher rather than racing another worker's reset.

**Step 1 — Register a test database connection:**

```bash
omop-config configure omop_alchemy
```

When prompted whether to configure a test database, answer **Y** and supply the connection details for your dedicated test PostgreSQL instance. It will be saved as `test_cdm_db_pg` with `test_only = true`.

> **Note on permissions**: the test suite disables FK constraint triggers during bulk vocabulary
> loads, an operation PostgreSQL restricts to superusers. Ensure the test database user has
> superuser privileges, or provision the user manually with `CREATE USER test SUPERUSER`.

**Step 2 — Run the tests:**

```bash
pytest -v tests/
```

PostgreSQL tests are excluded from a plain `pytest` run by default (see above); run `pytest -m postgresql` to include them, or `pytest -v tests/ -m postgresql` for verbose output. They still auto-skip if `test_cdm_db_pg` is not configured.
