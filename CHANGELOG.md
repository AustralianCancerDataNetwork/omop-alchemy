> [!NOTE]
> This file is no longer maintained. Release history from this point forward is in [GitHub Releases](https://github.com/AustralianCancerDataNetwork/OMOP_Alchemy/releases).

## 0.2.0
- Initial public release
- SQLAlchemy 2.0 typed OMOP CDM models
- Domain validation helpers
- Episode + event scaffolding

## 0.2.1
- minor modification to support chunked csv dedupe queries for larger load files 

## 0.2.2
- changed load_environment function to accept a string parameter for path to a .env file holding parameters for ENGINE and SOURCE_PATH to accommodate easier downstream usage

## 0.2.3
- added bulk_load_context for trusted bulk loads (e.g. Athena vocabulary)
- Disables FK enforcement where supported
- Suppresses autoflush during bulk load operations

## 0.2.4
- fstrings in logging throughout
- changed type handling for string columns that may contain numeric data to prevent errors during load

## 0.2.5
- optional commit on chunk load to reduce transaction size for large files

## 0.2.6
- extra episode demo and modify joined load strategy for conceptview objects

## 0.5.0
- significant refactor
- extract generic ORM loading, validation, and metadata into base library (orm-loader)
- elements now reusable in other downstream data models

## 0.5.1
- change get_engine_name to take optional parameter

## 0.5.2
- mostly just upversioning orm_loader base
- added table_has_rows convenience function

## 0.5.3
- upversioning orm_loader dependency to handle date parse updates for non-onco-branch vocab files

## 0.5.4
- modification of PK for concept_synonym table

## 0.5.5
- created some tests
- upversion orm_loader
- episode event convenience function

## 0.5.6
- added ConceptValidationMixin for both view and table mapped classes

## 0.5.7
- nullability in concept table

## 0.5.8
- moved complex mappers out of model module to stand alone

## 0.5.9
- refactoring vocabulary mapping lookups
- added typers for cdm type hinting

## 0.5.10
- added concept resolution registry

## 0.5.11
- updated concept resolution builder pattern

## 0.5.12
- created visit_occurrence view object to handle 'visits where a provider of specialty [x] was seen' (whether through visit provider, procedure provider, obs provider relationships)

## 0.6.0
- added PostgreSQL sequence reset utility for OMOP tables
- default reset scope excludes vocabulary-backed tables unless explicitly requested
- centralised maintenance table categories and reusable table selection helpers for future admin tasks
- added optional postgres dependency group and clearer missing-driver error for postgres engine creation
- added data-summary maintenance command for live OMOP table presence and row-count reporting
- added postgres foreign-key trigger management commands for bulk-load workflows
- added create-missing-tables maintenance command for schema bootstrap from ORM metadata
- added metadata-driven index disable/enable maintenance commands for bulk-load workflows
- optional tsvector handling
- upversion `orm-loader` dependency floor to `0.3.23` for generated-column-safe staged merges
- keep Athena vocabulary loading on tab-delimited input and improve load failure reporting
- refine PostgreSQL full-text sidecar support under `cdm.handlers.fulltext`
- add `omop-maint fulltext install`, `populate`, and `drop` commands
- document manual refresh expectations for sidecar `tsvector` columns

## 0.6.1
- upversion orm-loader
- set minimum versions per dependabot (dev and required deps)

## 0.6.2
- capped maximum `orm-loader` version to avoid pulling in future breaking changes

## 0.6.3
- fix CSV quote mode for Athena vocabulary loading: switch from `literal` to `auto` to prevent quoted concept names from overflowing `VARCHAR(255)` database columns
- make `chunksize=100_000` the default for `load-vocab-source` (was `None`/disabled); pass `--chunksize 0` to disable chunking explicitly
- **breaking:** `load-vocab-source` CLI now defaults `--merge-strategy` to `replace` (was `upsert`) to match the Python API default; `replace` overwrites source rows with matching primary keys but does not purge source-absent rows, so truncate vocabulary tables first for an exact refresh; pass `--merge-strategy upsert` to preserve existing conflicting rows
- **breaking:** CLI entry point renamed from `omop-maint` to `omop-alchemy`; update any scripts or aliases accordingly (saved `.omop-maint.toml` defaults files are unaffected)
- remove stale notebooks from repository

## 0.7.0
- major CLI overhaul: new `backends/` package (`Backend` ABC, `PostgresBackend`, `SQLiteBackend`) centralises all dialect-specific SQL, replacing logic scattered across CLI files
- new `@omop_command` decorator removes repeated connection/error-handling boilerplate from all CLI commands
- split the monolithic `cli_schema.py` into focused modules (`cli_schema_info`, `cli_schema_doctor`, `cli_schema_reconcile`, `cli_schema_tables`, `cli_schema_summary`); old import path kept as a re-export shim
- **breaking:** DB/engine configuration now goes entirely through `oa-configurator`'s config file; CLI commands no longer accept `--dotenv` or env-var based connection config
- accelerated vocabulary ingestion: pooled/reloaded connections, FK-trigger and index management around bulk loads, schema-qualified staging tables
- added PostgreSQL full-text sidecar (`tsvector`) install/populate/drop commands
- expanded CLI and API documentation: architecture overview and full command reference

## 0.8.0
- make Athena quote handling deterministic: `load-vocab-source` now defaults to `--quote-mode by_delimiter`, preserving literal quotes in tab-delimited exports while retaining explicit `csv`, `literal`, and `auto` modes
- fix `manage_indexes()` raising `KeyError` for `cost.cost_type_concept_id` under any schema-qualified connection: `cost` was the only model using an implicitly-named column index (`required_concept_fk(index=True)`), whose auto-generated name embeds the schema and drifts from the unqualified name the maintenance CLI looks up by
- fix `fact_relationship`'s primary key silently excluding `fact_id_1`/`fact_id_2`: `merge_table_args()` dropped the explicit `PrimaryKeyConstraint` because it's falsy (zero-length `.columns`) until attached to a table; the constraint never took effect and the table's real PK was only 3 of its intended 5 columns
- harden `merge_table_args()` to filter on `is None` rather than truthiness, so a Constraint/Index built from unresolved column-name strings can no longer be silently dropped from `__table_args__`
- **breaking:** removed the `index: bool` parameter from `required_concept_fk()`, `optional_concept_fk()`, `optional_fk()` (`cdm/base/column_helpers.py`); it produced schema-unsafe implicit index names and had no remaining call sites — index FK columns via an explicit `omop_index(...)` in `__table_args__` instead
- Added functional indices for lower() search on `concept` and `concept_synonym` tables
- `manage_indexes()` now runs `ANALYZE` immediately after creating any new index, so the planner picks it up right away instead of waiting on autovacuum
- added `--cluster/--no-cluster` flag to `indexes enable`, to skip the full heap rewrite on large vocabulary tables when disk headroom is limited
- `indexes enable` now reports a pre-existing expression-based index as skipped instead of raising, since SQLite's reflection can't see it to begin with (affects the new functional indexes under the test backend)
