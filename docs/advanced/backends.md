# Backend Compatibility

Every maintenance operation goes through a `Backend` (`PostgresBackend` or
`SQLiteBackend`). PostgreSQL implements the full `Backend` interface.
SQLite implements only what SQLite itself can do; everything else raises
`FeatureNotSupportedError` at call time rather than failing silently or
producing a partial result.

| Feature | PostgreSQL | SQLite |
| --- | --- | --- |
| Index existence check | Yes | Yes |
| Drop index if exists | Yes | Yes (unqualified — no schema concept) |
| `ANALYZE` | Yes | Yes |
| `VACUUM ANALYZE` | Yes | No |
| FK trigger management / status | Yes | No |
| FK constraint violation counting | Yes | No |
| Table clustering (`CLUSTER`) | Yes | No |
| Cluster index inspection | Yes | No |
| Functional-index expression normalization | Yes | No (SQLite never reflects expression-based indexes) |
| `TRUNCATE ... RESTART IDENTITY / CASCADE` | Yes | No |
| Sequence lookup / reset | Yes | No (SQLite has no sequences) |
| Full-text search | Yes — see [PostgreSQL Full-Text Search](fulltext.md) | No |
| Database backup / restore | Yes | No |

## What this means in practice

Maintenance CLI commands that rely on a not-supported feature raise
`FeatureNotSupportedError` on SQLite rather than doing nothing. In
particular, against a SQLite database:

- `indexes cluster` and the clustering step of `manage_indexes --enable`
  are unavailable.
- `truncate-tables` cannot use `RESTART IDENTITY`/`CASCADE`.
- `fulltext install` is unavailable entirely.
- `backup-database`/`restore-database` are unavailable entirely.
- FK trigger toggling and FK violation counting are unavailable.

SQLite remains fully supported for the core ORM/CDM layer (models, queries,
`create_missing_tables`, schema-provenance guarding) — these limitations are
specific to the maintenance operations listed above, which assume a
PostgreSQL-grade catalog.
