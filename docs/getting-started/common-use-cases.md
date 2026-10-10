# Common Use Cases

This pages details common use-cases and setups for users and how to wrap their established OMOP CDM with `omop-alchemy` and configure it with `oa-configurator`. 

!!! note "Important References"
    - [**`oa-configurator` config reference**](https://AustralianCancerDataNetwork.github.io/oa-configurator/config-reference/): Information about the config file created and stored by default at `~/.config/omop/config.toml`
    - [**`omop-alchemy` and relationship to OMOP CDM**](configuration.md#cdm-table-roles): Important how schemas capture specific tables.
    - [**`oa_configurator`'s architecture guide`**](https://AustralianCancerDataNetwork.github.io/oa-configurator/architecture/): Information about the core concepts, supported configuration templates, schema translation and provenance guard, and more.
    - [**`omop-alchemy`'s maintenance module**](maintenance.md): full command reference 


---

## Vocabulary tables in a separate schema, same server

!!! example "Scenario"
    - Your `Concept` table (and the rest of the vocabulary) lives in a `myvocab` schema
    - Vocabulary is separate from your clinical tables' schema. 
    - [Reading how `omop-alchemy` bundles tables in schemas](configuration.md#cdm-table-roles) reveals, that the `vocab_schema` configuration key is responsible for the `Concept` table

### Solution
Set `vocab_schema` to `myvocab` during interactive configuration
```bash
omop-config configure omop_alchemy
```

The resulting entry in `config.toml` will look like this:

```toml
[connections.<your-configured-connection>]
dialect = ...
host = ...
....

[databases.<your-configured-database>]
kind = "cdm"
connection = "<your-configured-connection>"
cdm_schema = "<regular schema name>"
vocab_schema = "myvocab"  # <- overwritten schema map

[tools.omop_alchemy]
cdm_db = "<your-configured-database>"
```

Every vocabulary-tagged table (see [Documentation for more details](configuration.md#cdm-table-roles)) now resolves into `myvocab` automatically.
This does not require any model changes. `results_schema` works the same way for results tables and is also defined in the [Documentation](configuration.md#cdm-table-roles)

### Troubleshooting

#### 1. You misconfigured the schema wrong for your select database

No issues. Just re-run the configuration command again:
```bash
omop-config configure omop_alchemy
```

The CLI wizard will guide you through the entire setup again. You can changed/modify settings. Previously configured fields are now the default and can just be accepted by pressing 'Enter'.

---

## Vocabulary on an entirely separate server

!!! example "Scenario"
    - Your entire CDM vocabulary lives on a separate physical DB server (e.g. a shared vocabnulary instance resued across multiple CDM deployments)
    - You checked the documentation for [supported dialects in `omop-alchemy`](https://AustralianCancerDataNetwork.github.io/oa-configurator/config-reference/#supported-dialects ) and confirmed that your separate DB server is supported


### Solution

Configure a separate `vocab_connection` during the setup of `omop-alchemy` when prompted:
```bash
omop-config configure omop_alchemy
```

```toml
[connections.cdm]  # <- your CDM connection
dialect       = "postgresql+psycopg"
host          = "cdm-db.internal"
database_name = "cdm"

[connections.vocab]  # <- your vocab connection
dialect       = "postgresql+psycopg"
host          = "vocab-db.internal"
database_name = "vocab"

[databases.cdm_db]
kind             = "cdm"
connection       = "cdm"
vocab_connection = "vocab"  # <- references your vocabulary DB
```

### When to use what

Rule of thumb: are you working with rows or with tables?

| You are... | Use | Example |
|---|---|---|
| Reading or writing rows of mapped classes | a session from `cdm_sessionmaker()` | `session.scalars(select(Concept).where(...))`, `session.add(Condition_Occurrence(...))`, `condition.condition_concept.concept_name` |
| Changing or inspecting tables themselves | the engine for that table's role, from `create_engines()` | CREATE/DROP/TRUNCATE, ANALYZE, ALTER, CREATE INDEX, `has_table`, `COUNT(*)` over a table, raw `text()` SQL |

```python
from omop_alchemy.config import get_cdm_context
from omop_alchemy.cross_database import cdm_sessionmaker

_, resolved = get_cdm_context()
primary, vocab = resolved.create_engines()
sessions = cdm_sessionmaker(resolved, primary=primary, vocab=vocab)
```

In practice:

- Application code works with rows, so it uses `cdm_sessionmaker()`. The session routes by role tag; vocabulary wins when a statement also names staging or another custom tag.
- Table-level work picks its engine explicitly: `vocab` for vocabulary tables, `primary` for everything else. The `omop-alchemy` maintenance commands already do this for you.

### Limits

- To filter one side by the other, use `filter_by_keys()`.
- Concept-set expressions on a clinical column (`expression_for()`, `runtime_concept_predicate()`) need `session=` to work across the two databases.
- A query joining tables from both databases raises `CrossDatabaseStatementError`. So does a statement sent to an engine that does not host its table.
- On a split deployment, raw `text()` SQL without a schema-tagged table is refused because the session cannot choose an engine. Use the primary or vocab engine directly for raw SQL. On a colocated deployment, the session uses the shared engine.
- No transaction covers both databases.
- Foreign keys between the two databases are not created, and `reconcile-schema` does not report them as missing.

---

## Migrating an existing deployment to a schema split

!!! example "Scenario"
    - You are moving from one schema holding everything to a real vocabulary/results splits, **or**
    - You are renaming a schema on a database `omop-alchemy` has already created tables in.
    - **Assumptions:**
        - your database for the CDM is named `my_db` in `config.toml`
            - there is an entry called `[databases.my_db]`, and
            - `[tools.omop_alchemy]` lists it as `cdm_db="my_db"`
        - you want to move all your tables governed by the interal `vocab` schema to schema `myvocab`

### Solution

[`oa-configurator`'s schema provenance guard](https://AustralianCancerDataNetwork.github.io/oa-configurator/architecture/#schema-provenance-guard) records which physical schema each role last resolved to, and refuses to run `create-missing-tables` if the configured schema for a role has silently changed since the last run. This mechanism is in place to stop a misconfiguration from creating an orphaned second copy of your tables. To make a genuine change deliberately:

1. Update `vocab_schema`/`results_schema`/`cdm_schema` in `config.toml` through reconfiguration  
    ```bash
    omop-config configure omop_alchemy
    ```
2. Move your actual data to the new schema yourself using access to the database.
    - This is **NEVER** done automatically to preserve data integrity from our end.
3. Record the new schema as the accepted baseline following the assumptions listed in "Scenario" above:
   ```bash
   omop-config acknowledge-schema-migration --database my_db --schema-tag vocab --new-schema myvocab --reason "moving vocab off the shared schema"
   ```
4. Once you've confirmed the new schema is correct, clean up the old one:
   ```bash
   omop-config drop-orphan-schema-tables --database cdm_db --schema old_vocab_schema --confirm
   ```
   Omit `--confirm` first to preview what would be dropped.

   Both commands live in `oa-configurator`, not `omop-alchemy` as they're generic over any `[databases.*]` entry, not CDM-specific.
