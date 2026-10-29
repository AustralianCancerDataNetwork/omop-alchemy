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

## Loading files

`load_file` is the entry point for bulk loads. It reads the file in bounded batches, stages the rows, and merges them into the table. During the merge each backend relaxes foreign key checks where it can, and DuckDB, which cannot, validates the constraints it never enforces. Load parent tables before their children.

```python
from sqlalchemy.orm import Session

with Session(engine) as session:
    MyTable.load_file(
        session,
        path="MY_TABLE.csv",
        dedupe=True,
        merge_strategy="upsert",
    )
    session.commit()
```

To insert ORM objects that reference each other without file loading, wrap the inserts in the backend's `bulk_load_context`. It disables foreign key enforcement and autoflush for the block, restores them on exit and rolls back on error. It is supported for SQLite and PostgreSQL only.

```python
from orm_loader.backends import resolve_backend

with Session(engine) as session:
    with resolve_backend(session).bulk_load_context(session):
        session.add_all(objects)
    session.commit()
```

## Optional PostgreSQL full-text search

OMOP Alchemy can optionally integrate with PostgreSQL full-text search for selected vocabulary text fields.

This feature is **not required** to use the library and is an optional enhancement rather than part of the core OMOP schema.

At the library level:

- query helpers can fall back to inline `to_tsvector(...)` expressions
- optional sidecar `tsvector` columns can be registered into SQLAlchemy metadata when they exist in the database

At the database level:

- PostgreSQL-only sidecar columns and optional GIN indexes can be managed through the maintenance CLI
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

![fulltext](../assets/images/oa-fulltext.png)

For the full design and query patterns, see:

- [PostgreSQL Full-Text Search](../advanced/fulltext.md)
- [Maintenance CLI](maintenance.md)
