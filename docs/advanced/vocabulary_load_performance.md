# Vocabulary Load Performance Tuning

This page documents how to get the fastest possible `load-vocab-source` runs, covering both PostgreSQL configuration and CLI parameter choices.

## How the load works (two phases)

Every table goes through two distinct phases:

| Phase | What happens | Relevant parameter |
|-------|--------------|--------------------|
| **1 — Staging load** | CSV is streamed into a temporary UNLOGGED staging table. On PostgreSQL the native `COPY` command is used (fast path); ORM/pandas chunked inserts are a fallback. | `--staging-chunk-size` *(fallback only)* |
| **2 — Merge** | Rows are moved from staging → target. With `--bulk-mode`, indexes are already dropped and FK triggers disabled for the duration of the full load. | `--merge-batch-size` |

## Pagination and why it is disabled by default

`--merge-batch-size` controls whether the staging → target merge is split into multiple transactions (pagination). When set to an integer, every table whose staging row count exceeds that value goes through three extra steps before any rows are moved:

1. `SELECT COUNT(*)` on the staging table
2. `CREATE INDEX ON staging (_rownum)` — a full index build on the staging table
3. N separate `COMMIT`s instead of one

Empirical timing on an 8 GB RAM dev machine with `concept_relationship` (~56 M rows):

| Configuration | Total load time |
|---------------|----------------|
| `--merge-batch-size 1_000_000` (old default) | ~120 min |
| `--merge-batch-size 20_000_000` | ~80 min |
| No pagination (default) | **~40 min** |

The staging index build alone on a 56 M-row table adds 10–15 minutes regardless of batch size. **The default is now `None` (no pagination).** Only set `--merge-batch-size` if your system cannot hold the full merge in a single transaction.

> **Warning:** Setting `--merge-batch-size` to a large number to "avoid" pagination does not help if that number is still smaller than the largest table. For `concept_relationship` (~56 M rows), any value below 56 M will trigger the index build. If you need pagination, set it to your actual memory limit; if you don't, leave it unset.

## PostgreSQL configuration

The next biggest bottleneck after pagination is `synchronous_commit=on` (the PostgreSQL default): every `COMMIT` blocks until WAL is flushed to disk. With pagination enabled and 5 M-row batches this means 11 synchronous disk flushes of ~1.5 GB each for `concept_relationship` alone.

### Recommended settings

Apply these via `postgresql.conf` or `-c` flags on whatever PostgreSQL instance you're loading into (per-package `docker-compose.yaml` files no longer exist; Docker orchestration for the OMOP stack now happens at the workspace root).

**8 GB host:**
```
synchronous_commit=off
checkpoint_timeout=30min
work_mem=256MB
maintenance_work_mem=2GB
shared_buffers=2GB
effective_cache_size=6GB
max_parallel_workers_per_gather=4
max_parallel_maintenance_workers=4
wal_compression=zstd
full_page_writes=off
```

**~4 GB host:**
```
synchronous_commit=off
checkpoint_timeout=30min
work_mem=128MB
maintenance_work_mem=512MB
shared_buffers=512MB
effective_cache_size=1GB
max_parallel_workers_per_gather=2
max_parallel_maintenance_workers=2
wal_compression=zstd
full_page_writes=off
```

> **Note:** `synchronous_commit=off` is safe for development and initial data loads — committed data is written to WAL buffers and flushed asynchronously. In the event of a crash, at most a few hundred milliseconds of commits may be lost. For vocabulary loads from a trusted source (Athena), this is always acceptable: just reload from source.

### Applying settings live without a container restart

`ALTER SYSTEM` writes to `postgresql.auto.conf`. However, any `-c` flag passed at container startup **takes precedence** over `ALTER SYSTEM`. Check the running container's startup flags before expecting a live reload to work.

Settings not overridden by a `-c` flag can be applied immediately:

```sql
ALTER SYSTEM SET synchronous_commit = 'off';
SELECT pg_reload_conf();
SHOW synchronous_commit;  -- confirm: should show 'off'
```

Settings that ARE overridden by `-c` (e.g. `checkpoint_timeout`) require a container restart to pick up an updated value.

To monitor whether WAL-write stalls are happening during a load:

```sql
SELECT query, state, wait_event_type, wait_event
FROM pg_stat_activity
WHERE state != 'idle';
-- With synchronous_commit=off: wait_event_type should NOT be 'IO' / 'WALWrite' on commits
```

## CLI parameters

### `--merge-batch-size`

Controls pagination of the staging → target merge. **Default: `None` (no pagination).**

Leave unset for the fastest load on a system with sufficient RAM. Set to a positive integer only when memory is constrained and you need to bound the size of individual transactions:

```
--merge-batch-size 5_000_000   # ~11 batches for concept_relationship; adds ~15 min overhead
```

### `--staging-chunk-size`

Controls ORM/pandas batch size for the CSV → staging phase. **Irrelevant on PostgreSQL** when the `COPY` fast-path is active (the default for well-formed Athena CSVs). Only tune this if you see `COPY` errors in the load output. Default: 100,000.

## Recommended invocation

```bash
omop-alchemy load-vocab-source \
  --athena-source /path/to/omop_vocab/ \
  --merge-strategy insert_if_empty \
  --bulk-mode
```

`--merge-strategy insert_if_empty` is the fastest strategy for a fresh (empty) database — it skips the delete phase entirely. `--bulk-mode` drops all indexes and disables FK triggers globally before loading and rebuilds after, which is much faster than per-table management for a full reload.

Pass `-vv` **before** the subcommand to see detailed progress from the orm-loader internals:

```bash
omop-alchemy -vv load-vocab-source --athena-source /path/to/omop_vocab/ ...
```
