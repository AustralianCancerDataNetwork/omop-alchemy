# Quickstart

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

The test suite includes PostgreSQL-specific tests that need a `test_cdm_db_pg` database configured in `~/.config/omop/config.toml`. Without it they fail, so deselect them with `pytest -m "not postgresql"`. They're resolved via oa-configurator's `isolated_test_database()`, wrapped in this repo's own `pg_db`/`pg_engine`/`pg_session` fixtures, and marked `@pytest.mark.postgresql`. Run them with `pytest -m postgresql`.

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

A plain `pytest` runs the PostgreSQL tests too. Run `pytest -m postgresql` for only those, or `pytest -v tests/ -m postgresql` for verbose output. They fail if `test_cdm_db_pg` is not configured, so use `pytest -m "not postgresql"` on a machine without PostgreSQL.
