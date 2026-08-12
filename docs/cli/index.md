# CLI Overview

The `omop-alchemy` command-line interface provides a suite of maintenance utilities for OMOP CDM databases. It is installed as part of the `omop-alchemy` package and is available on `PATH` after installation.

```bash
pip install omop-alchemy
omop-alchemy --help
```

## Command groups and flat commands

| Group / Command | What it covers |
|---|---|
| `info` | Environment inspection: package version, dependency status, connection state, per-command readiness |
| `doctor` | Health check: connection, schema drift, FK trigger state, FK violations, backup tooling |
| `reconcile-schema` | Compare ORM metadata against live column types, indexes, FK constraints, and cluster state |
| `create-missing-tables` | Detect and create ORM-managed OMOP tables that are absent from the database |
| `data-summary` | Row counts and existence state for ORM-managed tables |
| `load-vocab-source` | Load Athena CDM vocabulary CSV files |
| `analyze-tables` | ANALYZE or VACUUM ANALYZE selected tables to refresh planner statistics |
| `reset-sequences` | Reset owned PostgreSQL sequences to MAX(pk) + 1 |
| `truncate-tables` | Truncate selected ORM-managed tables |
| `indexes disable` / `enable` / `cluster` | Drop or recreate ORM-defined secondary indexes; physically cluster tables on their designated index |
| `foreign-keys disable` / `enable` / `status` / `validate` | Manage PostgreSQL RI trigger enforcement |
| `fulltext install` / `populate` / `drop` | Manage tsvector sidecar columns on vocabulary tables |
| `backup-database` | Create a pg_dump backup artifact |
| `restore-database` | Restore a pg_dump or psql backup artifact |

See the [Command Reference](reference.md) for full parameter details.

!!! note "Verbosity flag placement"
    The `--verbose` / `-v` flag is a **global option** and must appear **before** the
    subcommand name, not after it:

    ```
    omop-alchemy -v load-vocab-source   # ✓ correct
    omop-alchemy load-vocab-source -v   # ✗ flag is ignored
    ```

    Use `-v` for INFO level and `-vv` for DEBUG level.

---

## The `@omop_command` decorator

Most commands are decorated with `@omop_command`. This decorator handles all connection boilerplate so the command function body only needs to work with `conn` and `engine`.

### What it injects

Every decorated command receives:

- `conn` — a `_ConnContext` dataclass (see below)
- `engine` — a SQLAlchemy `Engine` ready to use
- `--dry-run` — injected on commands that support preview mode

No connection flags are injected; all configuration comes from oa_configurator.

### What it does behind the scenes

When a decorated command is invoked:

1. Calls `get_cdm_context()`, which loads `~/.config/omop/config.toml` (via `load_stack_config()`) and resolves whatever `OmopAlchemyConfig.cdm_db` currently names, returning `(pkg_config, resolved)`. Raises `RuntimeError` with a helpful message if no config file exists yet.
2. Calls `create_cdm_engine(resolved)` to build a SQLAlchemy engine (`resolved.create_engine()`, with `schema_translate_map` applied) and register its vocabulary cache identity.
3. Builds `conn` (`db_schema=resolved.schema_name`, `athena_source=pkg_config.athena_source_path`).
4. Prints a command header showing the connection, CDM schema, and run mode.
5. Calls the original function body with `(conn, engine, ...)`.
6. Catches `RuntimeError`, `SQLAlchemyError`, and `BackendNotSupportedError`; renders them as formatted errors and exits with code 1.

### Before and after

Without the decorator, every command would need this boilerplate:

```python
from omop_alchemy.config import create_cdm_engine, get_cdm_context

def my_command() -> None:
    pkg_config, resolved = get_cdm_context()
    engine = create_cdm_engine(resolved)
    try:
        # actual work here
        results = do_work(engine, db_schema=resolved.schema_name)
        console.print(render_results(results))
    except Exception as exc:
        handle_error(exc)
```

With the decorator, the function body is all that matters:

```python
@app.command("my-command")
@omop_command("my-command")
def my_command(conn, engine) -> None:
    results = do_work(engine, db_schema=conn.db_schema)
    console.print(render_results(results))
```

---

## The `conn` object

`conn` is a `_ConnContext` dataclass. It exposes:

| Attribute | Description |
|---|---|
| `conn.db_schema` | CDM schema name from the resolved database (e.g. `"omop"`) |
| `conn.athena_source` | Athena vocabulary CSV directory from `[tools.omop_alchemy]`'s `athena_source_path` field; `None` if not configured |
