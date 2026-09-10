# Common Use Cases

This pages details common use-cases and setups for users and how to wrap their established OMOP CDM with `omop-alchemy` and configure it with `oa-configurator`. 

!!! note "Important References"
    - [**`oa-configurator` config reference**](https://AustralianCancerDataNetwork.github.io/oa-configurator/config-reference/): Information about the config file created and stored by default at `~/.config/omop/config.toml`
    - [**`omop-alchemy` and relationship to OMOP CDM**](<missing link>): Important how schemas capture specific tables.
    - [**`oa_configurator`'s architecture guide`**](https://AustralianCancerDataNetwork.github.io/oa-configurator/architecture/): Information about the core concepts, supported configuration templates, schema translation and provenance guard, and more.
    - [**`omop-alchemy`'s maintenance module**](maintenance.md): full command reference 


---

## Vocabulary tables in a separate schema, same server

!!! note "Scenario"
    - Your `Concept` table (and the rest of the vocabulary) lives in a `myvocab` schema
    - Vocabulary is separate from your clinical tables' schema. 
    - [Reading how `omop-alchemy` bundles tables in schemas](<missing link>) reveals, that the `vocab_schema` configuration key is responsible for the `Concept` table

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
schema_name = "<regular schema name>"
vocab_schema = "myvocab"  # <- overwritten schema map

[tools.omop_alchemy]
cdm_db = "<your-configured-database>"
```

Every vocabulary-tagged table (see [Documentation for more details](<missing link>)) now resolves into `myvocab` automatically.
This does not require any model changes. `results_schema` works the same way for results tables and is also defined in the [Documentation](<missing-link>)

### Troubleshooting

#### 1. You misconfigured the schema wrong for your select database

No issues. Just re-run the configuration command again:
```bash
omop-config configure omop_alchemy
```

The CLI wizard will guide you through the entire setup again. You can changed/modify settings. Previously configured fields are now the default and can just be accepted by pressing 'Enter'.

---

## Vocabulary on an entirely separate server

!!! note "Scenario"
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

!!! warning "Queries spanning both databases"
    Reads that only touch vocabulary tables route to the `vocab` connection automatically. However, a query joining a vocabulary table against a clinical table (that lives in the `cdm` connection/DB) can't be answered by one physical connection in the underlying backend `SQLAlchemy`. `omop-alchemy` resolves those by querying each side separately and then merging the results in Python. Large queries therefore require significant memory budgets depending on the query.

---

## Migrating an existing deployment to a schema split

!!! note "Scenario"
    - You are moving from one schema holding everything to a real vocabulary/results splits, **or**
    - You are renaming a schema on a database `omop-alchemy` has already created tables in.
    - **Assumptions:**
        - your database for the CDM is named `my_db` in `config.toml`
            - there is an entry called `[databases.my_db]`, and
            - `[tools.omop_alchemy]` lists it as `cdm_db="my_db"`
        - you want to move all your tables governed by the interal `vocab` schema to schema `myvocab`

### Solution

[`oa-configurator`'s schema provenance guard](https://AustralianCancerDataNetwork.github.io/oa-configurator/architecture/#schema-provenance-guard) records which physical schema each role last resolved to, and refuses to run `create-missing-tables` if the configured schema for a role has silently changed since the last run. This mechanism is in place to stop a misconfiguration from creating an orphaned second copy of your tables. To make a genuine change deliberately:

1. Update `vocab_schema`/`results_schema`/`schema_name` in `config.toml` through reconfiguration  
    ```bash
    omop-config configure omop_alchemy
    ```
2. Move your actual data to the new schema yourself using access to the database.
    - This is **NEVER** done automatically to preserve data integrity from our end.
3. Record the new schema as the accepted baseline following the assumptions listed in "Scenario" above:
   ```bash
   omop-alchemy acknowledge-schema-migration --database -my_db --role vocab --new-schema myvocab --reason "moving vocab off the shared schema"
   ```
4. Once you've confirmed the new schema is correct, clean up the old one:
   ```bash
   omop-alchemy drop-orphan-schema-tables --database cdm_db --schema old_vocab_schema --confirm
   ```
   Omit `--confirm` first to preview what would be dropped.


