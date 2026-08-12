# Maintenance CLI

The `omop-alchemy` maintenance CLI handles everything you need to operate an OMOP CDM
database: creating tables, loading Athena vocabularies, managing indexes and foreign key
enforcement, running health checks, and taking backups. It talks directly to a SQLAlchemy
engine, so all connection details are controlled by the same engine URL configuration you
use for the ORM.

> **Alpha status**
> Treat this CLI as alpha operational tooling. Interfaces and behavior may still change.

---

## Connection setup

Database connection and CDM schema come from [oa_configurator](../getting-started/configuration.md) — no per-command flags needed. Run `omop-config configure omop_alchemy` once to create `~/.config/omop/config.toml`, then every `omop-alchemy` command picks it up automatically.

---

## Backend support

Some commands depend on PostgreSQL-specific features and will return a clear error
if you run them against SQLite.

| Command group | Requires PostgreSQL | Why |
| --- | --- | --- |
| `load-vocab-source` | No (PostgreSQL + SQLite) | Uses ORM CSV loader; `--bulk-mode` and `--db-schema` are PostgreSQL-only |
| `indexes` | No (cluster apply is PostgreSQL-only) | Index DDL is standard SQL; `CLUSTER` is PostgreSQL |
| `create-missing-tables`, `reconcile-schema`, `data-summary`, `info`, `doctor` | No | Pure SQLAlchemy metadata operations |
| `reset-sequences` | Yes | PostgreSQL sequences (`SETVAL`) |
| `truncate-tables` | Yes | PostgreSQL `TRUNCATE` with `RESTART IDENTITY` and `CASCADE` |
| `foreign-keys` | Yes | PostgreSQL internal RI trigger `ALTER TABLE ... DISABLE/ENABLE TRIGGER ALL` |
| `analyze-tables` | No (`--vacuum` is PostgreSQL-only) | `ANALYZE` is standard; `VACUUM ANALYZE` is PostgreSQL |
| `fulltext` | Yes | PostgreSQL `tsvector`, `tsquery`, and `GIN` indexes |
| `backup-database`, `restore-database` | Yes | `pg_dump` / `pg_restore` / `psql` |

---

## Workflow guides

### Fresh database setup

Use this when you are starting with an empty database and want to get an OMOP schema
populated from scratch.

```bash
# 1. Create any OMOP tables that don't exist yet (safe to run on an existing DB)
omop-alchemy create-missing-tables --dry-run  # preview first
omop-alchemy create-missing-tables

# 2. Load Athena vocabulary files
omop-alchemy load-vocab-source --athena-source ./athena_files

# 3. Reset sequences so new clinical inserts start above vocabulary IDs (PostgreSQL)
omop-alchemy reset-sequences
```

The `create-missing-tables` command compares ORM metadata against the live schema and
creates only what is missing. It is idempotent — running it again on a populated database
does nothing.

`load-vocab-source` automatically creates any missing vocabulary tables before loading,
so you can run it immediately after step 1 or even skip step 1 for vocabulary-only setups.

---

### Full vocabulary reload

Run this when you download a new Athena export and want to replace the existing vocabulary.

```bash
# Suspend FK enforcement and drop indexes so loading is fast
omop-alchemy foreign-keys disable
omop-alchemy indexes disable --vocab

# Clear existing vocabulary data
omop-alchemy truncate-tables --scope vocabulary --restart-identities --yes

# Load new vocabulary (bulk-mode is on by default: does not re-toggle indexes per table)
omop-alchemy load-vocab-source --athena-source ./athena_files --merge-strategy replace

# Rebuild indexes and re-enable FK enforcement
omop-alchemy indexes enable --vocab
omop-alchemy foreign-keys enable --strict

# Refresh full-text sidecar vectors (if installed)
omop-alchemy fulltext populate
```

**About `--bulk-mode` (default on PostgreSQL):**
`load-vocab-source` disables FK triggers and drops vocabulary indexes once before the
load loop, then rebuilds them once at the end. This is much faster than the alternative
of toggling per table — for a full Athena export the difference can be 10–20×. SQLite
ignores this flag. Pass `--no-bulk-mode` if you need per-table rollback safety.

**About `--merge-strategy replace`:**
`replace` overwrites rows whose primary keys occur in the CSV; it does not delete rows
that are absent from the source. The explicit `truncate-tables` step above is therefore
required when the database must exactly mirror a new Athena export. Use `upsert` for
incremental vocabulary patches that must preserve existing conflicting rows. Use
`insert_if_empty` as the fastest path when the target tables are guaranteed empty.

**About `--quote-mode by_delimiter`:**
The default preserves double-quotes as data in tab-delimited Athena exports and uses
RFC-4180 quoting for comma-delimited files. Use `--quote-mode csv` only when the source
genuinely wraps fields in CSV quotes; `--quote-mode literal` forces quotes to remain data.
The `auto` mode samples content and is less predictable for large Athena files.

**About `--strict` on `foreign-keys enable`:**
`--strict` validates all FK relationships before re-enabling RI triggers. If violations
are found, no triggers are re-enabled and you get a report of the problematic rows.
Omit `--strict` to re-enable unconditionally.

---

### ETL bulk load cycle

Use this before and after a large clinical data load to avoid the overhead of FK and
index maintenance during insertion.

```bash
# Before your ETL runs: suspend enforcement and remove indexes
omop-alchemy foreign-keys disable
omop-alchemy indexes disable

# --- your ETL process runs here ---

# After ETL: restore state
omop-alchemy reset-sequences
omop-alchemy indexes enable
omop-alchemy foreign-keys enable --strict
omop-alchemy analyze-tables --scope clinical
```

`analyze-tables` refreshes planner statistics after a large load so query plans don't
degrade. `--scope clinical` targets only clinical tables; omit `--scope` to analyze
everything.

`reset-sequences` ensures that any auto-increment columns are positioned above the
maximum key value present in the table. This matters when your ETL inserts explicit IDs
(common in OMOP) — without a reset, the next ORM insert would try to reuse an ID that
already exists.

---

### Health checks

**Quick read-only check:**

```bash
omop-alchemy doctor
```

Runs a fast, non-destructive pass over connection readiness, schema drift, and FK
trigger status. The output tells you what is wrong and what to do about it.

**Deep FK validation (PostgreSQL):**

```bash
omop-alchemy doctor --deep
```

Adds a full FK constraint scan — it actually queries the data to find rows that violate
declared FK relationships. On large databases this can be slow; use it when you suspect
data integrity issues after an ETL or vocabulary patch.

**Full environment introspection:**

```bash
omop-alchemy info
```

Shows the active engine URL, installed backend driver, OMOP Alchemy version, optional
dependency state (orm-loader, psycopg2/psycopg, etc.), and which maintenance commands
are available given the current backend. Run this first when diagnosing "why doesn't
this command work".

**When doctor reports a problem:**

| Doctor output | What it means | Fix |
| --- | --- | --- |
| Schema drift: missing tables | ORM has tables not in DB | `create-missing-tables` |
| Schema drift: extra tables | DB has tables ORM doesn't know | Review manually; may be custom extensions |
| FK triggers disabled | RI enforcement was suspended | `foreign-keys enable` or `foreign-keys enable --strict` |
| FK violations found | Data fails FK constraints | Investigate data, then `foreign-keys enable --strict` |

---

### Schema drift

The `reconcile-schema` command compares your ORM metadata against the live database and
reports what it finds:

```bash
omop-alchemy reconcile-schema
omop-alchemy reconcile-schema --dry-run  # same output, no changes
```

Output categories:

- **missing** — table is in ORM metadata but not in the database. Fix: `create-missing-tables`.
- **extra** — table is in the database but not in ORM metadata. This can mean custom tables, staging tables, or leftover artifacts. The CLI does not touch these.
- **matched** — table exists in both and metadata is consistent.
- **drifted** — table exists in both but column definitions differ (types, nullability, defaults). The CLI does not auto-migrate; you need to handle schema migrations manually.

For safe deployment: run `reconcile-schema` first, then `create-missing-tables --dry-run`,
then `create-missing-tables`.

---

### Full-text search sidecars

Full-text search support adds `tsvector` sidecar columns (and `GIN` indexes) to the
`concept` and `concept_synonym` tables, enabling fast text search over vocabulary.

```bash
# Install the sidecar columns and indexes (once, after vocabulary tables exist)
omop-alchemy fulltext install

# Populate sidecar vectors from current vocabulary data
omop-alchemy fulltext populate
```

**You must rerun `fulltext populate` after every vocabulary reload.** Sidecar vectors
do not auto-refresh when the underlying concept data changes.

To remove the sidecars:

```bash
omop-alchemy fulltext drop
```

The `--regconfig` option controls the PostgreSQL text search configuration
(default `english`). For multilingual vocabularies, use a suitable config such as
`simple`.

For query-side usage and optional ORM metadata registration, see
[PostgreSQL Full-Text Search](../advanced/fulltext.md).

---

### Backup and restore

These commands wrap `pg_dump` and `pg_restore` / `psql`. PostgreSQL client tools must
be installed and on `PATH`.

```bash
# Create a backup (custom format is recommended — smaller and restorable in parallel)
omop-alchemy backup-database \
  --output-path ./cdm-backup.dump \
  --format custom

# Restore into a target database (the DB must already exist and be empty)
omop-alchemy restore-database ./cdm-backup.dump \
  --format custom
```

**Format comparison:**

| Format | File extension | Restore tool | Advantages |
| --- | --- | --- | --- |
| `custom` (default) | `.dump` | `pg_restore` | Compressed; supports parallel restore (`-j`) and selective restore |
| `plain` | `.sql` | `psql` | Human-readable SQL; editable but much larger |

**Restore caveats:**
- The target database must already exist. The CLI does not create or drop databases.
- For `plain` format, the schema is embedded in the SQL dump; no selective schema restore is possible.
- For `custom` format, `pg_restore` can be invoked manually with `-n <schema>` for selective schema restore.

Use `--dry-run` on `backup-database` to see the `pg_dump` command that would be run
without executing it.

---

## Recovery: when things go wrong

### Bulk load or vocabulary reload fails mid-way

If `load-vocab-source` (with `--bulk-mode`) or your ETL process fails after FK triggers
and indexes have been disabled, they stay disabled. The database continues to accept
writes but does not enforce FK constraints, and queries may use slow sequential scans.

To recover:

```bash
omop-alchemy indexes enable --vocab   # or without --vocab if you disabled all indexes
omop-alchemy foreign-keys enable
```

If you used `--strict` originally and now have data violations:

```bash
omop-alchemy foreign-keys validate   # see what's broken
# fix the data
omop-alchemy foreign-keys enable --strict
```

### FK validation fails after `enable --strict`

```bash
omop-alchemy foreign-keys validate
```

This reports exactly which tables have violations, which constraints are affected, and
how many rows fail. Fix the data, then retry `foreign-keys enable --strict`.

If you need to re-enable FK triggers despite the violations (for example, to allow the
application to run while you investigate), use `foreign-keys enable` without `--strict`.

### Sequences are out of sync after a bulk insert

After any load that inserts explicit primary key values:

```bash
omop-alchemy reset-sequences          # all managed tables
omop-alchemy reset-sequences --vocab  # vocabulary tables only
```

`reset-sequences` sets each owned sequence to `MAX(pk) + 1`. It reports every table
it touches and the old/new sequence positions.

---

## Command reference

| Command | Purpose | Key options | Backend |
| --- | --- | --- | --- |
| `info` | Inspect CLI readiness, backend, and dependency state | `--vocab` | All |
| `doctor` | Read-only health check: connection, schema, FK state | `--deep`, `--vocab` | All (`--deep` PostgreSQL-focused) |
| `data-summary` | Show managed tables and row counts | `--vocab`, `--include-missing` | All |
| `reconcile-schema` | Compare ORM metadata vs live schema | `--vocab`, `--dry-run` | All |
| `create-missing-tables` | Create OMOP tables absent from DB | `--dry-run`, `--no-vocab` | All |
| `load-vocab-source` | Load Athena vocabulary CSVs | `--athena-source`, `--merge-strategy`, `--quote-mode`, `--bulk-mode/--no-bulk-mode`, `--dry-run` | PostgreSQL, SQLite |
| `truncate-tables` | Truncate selected tables | `--scope`, `--table`, `--yes`, `--cascade`, `--restart-identities` | PostgreSQL |
| `reset-sequences` | Reset owned PK sequences to `MAX(pk) + 1` | `--dry-run`, `--vocab` | PostgreSQL |
| `foreign-keys disable` | Suspend FK RI trigger enforcement | `--vocab`, `--dry-run` | PostgreSQL |
| `foreign-keys enable` | Re-enable FK RI trigger enforcement | `--strict`, `--vocab`, `--dry-run` | PostgreSQL |
| `foreign-keys validate` | Report FK constraint violations | `--vocab` | PostgreSQL |
| `foreign-keys status` | Show current trigger enable/disable state | `--vocab` | PostgreSQL |
| `analyze-tables` | Refresh planner statistics | `--scope`, `--table`, `--vacuum` | PostgreSQL, SQLite (`--vacuum` PostgreSQL-only) |
| `indexes disable` | Drop ORM-defined secondary indexes | `--vocab`, `--dry-run` | All |
| `indexes enable` | Recreate ORM-defined secondary indexes | `--vocab`, `--dry-run` | All (cluster on PostgreSQL) |
| `fulltext install` | Add tsvector sidecar columns to vocabulary tables | `--regconfig`, `--no-create-indexes` | PostgreSQL |
| `fulltext populate` | Populate sidecar tsvector vectors | `--regconfig` | PostgreSQL |
| `fulltext drop` | Remove tsvector sidecar columns and indexes | | PostgreSQL |
| `backup-database` | Create a `pg_dump` backup artifact | `--output-path`, `--format`, `--dry-run` | PostgreSQL |
| `restore-database` | Restore a backup artifact into the target DB | `--format` (required), `--dry-run` | PostgreSQL |
