# Basic setup

## Connecting directly via SQLAlchemy

OMOP_Alchemy does not require any special database wrapper. 

You can always connect to your database using plain SQLAlchemy and work with engines, connections, and sessions exactly as you would in any other project.

```python
import sqlalchemy as sa
import sqlalchemy.orm as so
from omop_alchemy.cdm.model.vocabulary import Concept

engine = sa.create_engine(
    "postgresql+psycopg://user:password@localhost:5432/omop",
    future=True,
    echo=False,
)

with so.Session(engine) as session:
    concepts = (
        session.query(Concept)
        .filter(Concept.domain_id == "Drug")
        .limit(10)
        .all()
    )
```


```python
with so.Session(engine) as session:
    session.add(obj)
    session.commit()
```

**Database configuration** is handled by oa_configurator. See [Configuration](configuration.md) for how to set up `~/.config/omop/config.toml`.

## Session & Engine Management for Bulk Operations

ORM-loader module provides context managers for safely relaxing database constraints during high-volume operations such as CSV loads, staging-table merges, and backfills.

These utilities temporarily change database behaviour and guarantee restoration even on failure.

`bulk_load_context` temporarily adjusts session-level behaviour to make bulk inserts faster:

* Optionally disables foreign key enforcement
* Optionally disables SQLAlchemy autoflush
* Ensures all settings are restored on exit
* Rolls back the session if an exception occurs

This context manager is session-scoped and safe to use alongside ORM loaders, and is supported for sqlite and postgres backends.

```python
@contextmanager
def bulk_load_context(
    session: Session,
    *,
    disable_fk: bool = True,
    no_autoflush: bool = True,
):
```

Usage:

```python
from sqlalchemy.orm import Session
from orm_loader.helpers import bulk_load_context

with Session(engine) as session:
    with bulk_load_context(session):
        MyTable.load_csv(
            session,
            path="MY_TABLE.csv",
            dedupe=True,
            merge_strategy="upsert",
        )

    session.commit()
```

`engine_with_replica_role` enforces replica mode at the engine level, meaning:

* All new connections opened during the context run with `session_replication_role = replica`
* The role is restored to DEFAULT afterward

This is supported for postgres only.

Use engine_with_replica_role when:

* Creating / refreshing materialized views
* Running schema-level operations that might trigger independent sessions
* Using tooling that opens its own connections

## Optional PostgreSQL full-text search

OMOP Alchemy can optionally integrate with PostgreSQL full-text search for selected
vocabulary text fields.

This feature is **not required** to use the library and is intentionally treated as an
optional enhancement rather than part of the core OMOP schema.

At the library level:

- query helpers can fall back to inline `to_tsvector(...)` expressions
- optional sidecar `tsvector` columns can be registered into SQLAlchemy metadata when
  they exist in the database

At the database level:

- PostgreSQL-only sidecar columns and optional GIN indexes can be managed through the
  maintenance CLI
- those sidecar columns are populated explicitly rather than auto-generated

Typical maintenance workflow:

```bash
omop-alchemy fulltext install
omop-alchemy fulltext populate
```

If you later reload vocabulary data, rerun:

```bash
omop-alchemy fulltext populate
```

For the full design and query patterns, see:

- [PostgreSQL Full-Text Search](../advanced/fulltext.md)
- [Maintenance CLI](maintenance.md)
