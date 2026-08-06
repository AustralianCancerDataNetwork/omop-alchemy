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

The test suite includes PostgreSQL-specific tests that skip automatically unless a `test_cdm_db` database is configured in `~/.config/omop/config.toml`. Tests are marked with `@pytest.mark.requires_database("test_cdm_db")` and skipped at collection time when the database is absent — no manual filtering required.

> **This test database is destructive.** The test suite drops and recreates the entire `public`
> schema on every run. `test_cdm_db` must point to a **dedicated, empty test database**, never
> to a database that contains real data. The test suite enforces this: it fails loudly (not skips) if the
> configured database is not marked `test_only = true` in your config.

**Step 1 — Register a test database connection:**

```bash
omop-config configure omop_alchemy
```

When prompted whether to configure a test database, answer **Y** and supply the connection details for your dedicated test PostgreSQL instance. It will be saved as `test_cdm_db` with `test_only = true`.

> **Note on permissions**: the test suite disables FK constraint triggers during bulk vocabulary
> loads, an operation PostgreSQL restricts to superusers. Ensure the test database user has
> superuser privileges, or provision the user manually with `CREATE USER test SUPERUSER`.

**Step 2 — Run the tests:**

```bash
pytest -v tests/
```

PostgreSQL tests auto-skip when `test_cdm_db` is not configured; all other tests run regardless.
