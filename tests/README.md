# Running the test suite

## Quick start

```bash
# Unit and SQLite tests — no database required
uv run --extra dev pytest -m "not requires_database"

# PostgreSQL integration tests — requires a configured test_cdm_db
uv run --extra dev --extra postgres pytest -m requires_database -v
```

## PostgreSQL integration tests

PostgreSQL provisioning belongs to the workspace stack rather than this package;
there is no package-local Compose file. Configure its dedicated test database
using `omop-config configure omop_alchemy`. The resulting `test_cdm_db`
connection must have `test_only = true`—the test plugin rejects an ordinary
connection because these tests recreate its `public` schema.

```bash
# Run the complete suite; database tests skip if test_cdm_db is absent.
uv run --extra dev --extra postgres pytest -v
```

## Test markers

| Marker | Meaning |
|--------|---------|
| *(none)* | Runs on SQLite, no external dependencies |
| `requires_database("test_cdm_db")` | Requires the configured PostgreSQL test database |

## Fixture data

`tests/fixtures/athena_source/` contains a minimal set of Athena vocabulary
CSVs (7 concepts) used to seed the SQLite test database. These are committed
to the repo and are sufficient for all tests not marked `requires_database`.
