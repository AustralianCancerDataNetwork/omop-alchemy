# Running the test suite

## Quick start

```bash
# Unit and SQLite tests
uv run --extra dev pytest

# PostgreSQL integration tests — requires a configured test_cdm_db_pg
uv run --extra dev --extra postgres pytest -m postgresql -v
```

## PostgreSQL integration tests

PostgreSQL provisioning belongs to the workspace stack rather than this package;
there is no package-local Compose file. Configure its dedicated test database
using `omop-config configure omop_alchemy`. The resulting `test_cdm_db_pg`
connection must have `test_only = true`—the test plugin rejects an ordinary
connection because these tests recreate its `public` schema.

```bash
# Run the complete suite; database tests skip if test_cdm_db_pg is absent.
uv run --extra dev --extra postgres pytest -v
```

## Test markers

| Marker | Meaning |
|--------|---------|
| *(none)* | Runs on SQLite, no external dependencies |
| `postgresql` | Requires the configured PostgreSQL test database |

## Fixture data

`tests/fixtures/athena_source/` contains a minimal set of Athena vocabulary
CSVs (7 concepts) used to seed the SQLite test database. These are committed
to the repo and are sufficient for the default suite.
