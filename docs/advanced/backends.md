# Backend Compatibility

Every maintenance operation goes through a `Backend`
(`PostgresBackend`, `SQLiteBackend`, or `DuckDBBackend`). PostgreSQL
implements the full `Backend` interface. SQLite and DuckDB each implement
only what their own engine can do; everything else raises
`FeatureNotSupportedError` at call time rather than failing silently or
producing a partial result. Run `omop-alchemy info` against your target
database to see the exact per-command support matrix, derived live from
which methods the resolved backend class overrides.

| Feature | PostgreSQL | SQLite | DuckDB |
| --- | --- | --- | --- |
| Index existence check | Yes | Yes | Yes |
| Drop index if exists | Yes | Yes (unqualified — no schema concept) | Yes |
| `ANALYZE` | Yes | Yes | Yes |
| `VACUUM ANALYZE` | Yes | No | Yes |
| FK trigger management / status | Yes | No | No (no trigger mechanism) |
| FK constraint violation counting | Yes | No | Yes (anti-join; also covers deferred FKs, see below) |
| Table clustering (`CLUSTER`) | Yes | No | No |
| Cluster index inspection | Yes | No | No |
| Functional-index expression normalization | Yes | No (SQLite never reflects expression-based indexes) | Yes |
| `TRUNCATE ... RESTART IDENTITY` | Yes | No | Yes |
| `TRUNCATE ... CASCADE` | Yes | No | No — see below |
| Sequence lookup / reset | Yes | No (SQLite has no sequences) | Yes |
| Full-text search | Yes — see [PostgreSQL Full-Text Search](fulltext.md) | No | No |
| Database backup / restore | Yes | No | No |

## What this means in practice

A command that relies on a not-supported feature raises
`FeatureNotSupportedError` rather than doing nothing. Against SQLite:

- `indexes cluster` and the clustering step of `indexes enable` are unavailable.
- `truncate-tables` cannot use `RESTART IDENTITY`/`CASCADE`.
- `fulltext install` is unavailable entirely.
- `backup-database`/`restore-database` are unavailable entirely.
- FK trigger toggling and FK violation counting are unavailable.


## Duck-DB specifics

- `fulltext install`, `backup-database`/`restore-database`, table
  clustering, and FK trigger toggling/status are unavailable, same as SQLite.
- `truncate-tables --restart-identities` works; `--cascade` does not (see below).
- `reset-sequences` and `foreign-keys validate` both work, unlike on SQLite.

### Deferred foreign keys

DuckDB can create a foreign key inline at `CREATE TABLE` time, but cannot execute the deferred `ALTER TABLE ADD CONSTRAINT` a cyclic or self-referential foreign key otherwise needs.
Instead, we introduce a shim in oa-configurator that no-ops these statements. Marking specific columns with `use_alter=True` routes this into the deferred path:

- `domain.domain_concept_id`, `vocabulary.vocabulary_concept_id`, `concept_class.concept_class_concept_id`:  break the real `concept`/`concept_class`/`domain`/`vocabulary` reference cycle. Load these four tables in that order (matches `ATHENA_LOAD_ORDER`).
- `relationship.reverse_relationship_id`, `episode.episode_parent_id`,   `visit_occurrence.preceding_visit_occurrence_id`, `visit_detail.preceding_visit_detail_id`, `visit_detail.parent_visit_detail_id`: DuckDB would reject any bulk-load row inserted before its own self-referenced parent row exists, with no FK-toggle to relax that temporarily.

All FKs falling into this specification are never enforced at the database level on DuckDB. `DatabaseBackend.deferred_fk_constraints()`
- `orm-loader` is the single source of truth for which FKs this applies to: `reconcile-schema`,`foreign-keys validate`, and `doctor --deep` all consult it and run an anti-join check in its place.


### `merge_strategy="replace"` requires `index_strategy` to drop indexes

DuckDB's `replace` merge strategy updates matching rows in place as it rejects deleting a still-referenced row outright, with no FK-toggle or deferred-constraint escape hatch (unlike the delete-then-reinsert every other backend uses). That UPDATE fails against a live index on an updated column, so `index_strategy="keep"` is incompatible with `merge_strategy="replace"` on this backend specifically. Use the default `index_strategy="auto"` (which drops and rebuilds indexes around the merge) instead.

### `truncate-tables --cascade` is not supported

DuckDB's parser accepts `CASCADE` on a `TRUNCATE` statement but silently ignores it.Pass the real set of dependent tables explicitly instead; `truncate-tables` already orders whatever set you give it correctly regardless of input order.

## Why `supports_fast_load` is `False`

DuckDB's loader uses the same generic staged-merge path as SQLite rather than a native bulk-COPY equivalent. This is a deliberate choice as the generic path is fast enough at measured CDM-scale load volumes.
