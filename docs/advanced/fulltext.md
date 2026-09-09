# PostgreSQL Full-Text Search

OMOP Alchemy includes an **optional** PostgreSQL full-text search integration for selected vocabulary text fields.

This feature is deliberately bolt-on:

- OMOP_Alchemy works without the full-text search feature (i.e. optional)
- the ORM models do not require the extra database columns
- the query helpers can fall back to inline `to_tsvector(...)` expressions when the sidecar columns are not installed

This is useful when you want faster repeated text-search workloads on PostgreSQL, without forcing every environment to carry extra full-text infrastructure.

---

## What It Covers

The current full-text support targets:

- `concept.concept_name`
- `concept_synonym.concept_synonym_name`

These helpers are methods on the resolved `Backend` instance:

```python
from omop_alchemy.backends import resolve_backend

backend = resolve_backend(engine)
backend.concept_name_tsvector_expression()
backend.concept_synonym_name_tsvector_expression()
```

These helpers return the best available expression for the configured environment:

- if the optional sidecar `tsvector` columns are registered in metadata, they return the stored column
- otherwise they fall back to an inline computed PostgreSQL expression using `to_tsvector(...)`

### Example (PostgreSQL Documentation)

A `tsvector` value is a sorted list of distinct lexemes (normalised word forms). Sorting and duplicate elimination are applied automatically during input.

```sql
SELECT 'a fat cat sat on a mat and ate a fat rat'::tsvector;

-- Result:
-- 'a' 'and' 'ate' 'cat' 'fat' 'mat' 'on' 'rat' 'sat'
---
```

## Quick Activation

To enable the optional full-text sidecars in a PostgreSQL environment:

```bash
omop-alchemy fulltext install
omop-alchemy fulltext populate
```

If your running Python process should use the stored sidecar columns through ORM metadata, register them once at startup:

```python
from omop_alchemy.backends import resolve_backend

backend = resolve_backend(engine)
backend.register_fulltext_metadata()
```

That is enough to activate the feature. The rest of this page explains when to use it and how to operate it safely.

## When To Use It

Use the optional full-text feature when:

- you are on PostgreSQL
- you run repeated vocabulary-name or synonym-name search workloads
- inline `to_tsvector(...)` search is becoming a bottleneck

Skip it when:

- you only do occasional text search
- you want to keep the database as close as possible to the base OMOP schema
- you do not want to own the refresh cycle for derived search columns

---

## Why It Is Optional

Full-text search is useful, but it also introduces operational tradeoffs:

- extra columns in the database
- optional GIN indexes
- explicit backfill / refresh work
- PostgreSQL-specific behavior

Many users only need occasional text matching and are perfectly fine with inline search expressions. Others want fast repeated full-text lookups across large vocabularies and are happy to manage the extra schema objects.

OMOP Alchemy therefore treats full-text sidecars as an **optional PostgreSQL enhancement**, not as part of the core required OMOP schema.

---

## Two Modes

### 1. Inline Expression Mode

This requires no schema changes.

```python
import sqlalchemy as sa
from sqlalchemy.orm import Session

from omop_alchemy.backends import resolve_backend
from omop_alchemy.cdm.model.vocabulary import Concept

backend = resolve_backend(engine)
query = sa.func.plainto_tsquery("english", "edoxaban")

with Session(engine) as session:
    rows = (
        session.query(Concept)
        .filter(backend.concept_name_tsvector_expression() == query)
        .all()
    )
```

In practice you will often want the PostgreSQL full-text match operator rather than equality:

```python
vector = backend.concept_name_tsvector_expression()
query = sa.func.plainto_tsquery("english", "edoxaban")

stmt = sa.select(Concept).where(vector.op("@@")(query))
```

This mode is simple and portable at the library level, but PostgreSQL must compute the vector expression at query time unless the planner can otherwise optimize it.

### 2. Stored Sidecar Mode

This mode adds real `tsvector` columns to the database and optionally GIN indexes.

Once installed and registered, the helper functions point at the stored columns instead of recomputing vectors inline.

This is the mode you want when:

- you run frequent vocabulary search queries
- you care about PostgreSQL full-text query performance
- you are comfortable managing the sidecar lifecycle

---

## Lifecycle

The maintenance CLI manages the full-text sidecars through:

```bash
omop-alchemy fulltext install
omop-alchemy fulltext populate
omop-alchemy fulltext drop
```

Typical workflow:

```bash
omop-alchemy fulltext install
omop-alchemy fulltext populate
```

If you later reload or update vocabulary data, refresh the stored vectors with:

```bash
omop-alchemy fulltext populate
```

If you want to remove the feature completely:

```bash
omop-alchemy fulltext drop
```

---

## Important Behavior

The current implementation uses **ordinary nullable sidecar `tsvector` columns**, not generated columns and not trigger-managed columns.

That means:

- `install` creates the columns and optional GIN indexes
- `populate` backfills or refreshes the values
- future data changes are **not** reflected automatically until you repopulate

This is a deliberate choice because it keeps the feature explicit and easier to manage alongside bulk vocabulary loads.

---

## Querying Pattern

A typical PostgreSQL query looks like this:

```python
import sqlalchemy as sa
from sqlalchemy.orm import Session

from omop_alchemy.backends import resolve_backend
from omop_alchemy.cdm.model.vocabulary import Concept

backend = resolve_backend(engine)
vector = backend.concept_name_tsvector_expression()
query = sa.func.plainto_tsquery("english", "direct oral anticoagulant")

stmt = (
    sa.select(
        Concept.concept_id,
        Concept.concept_name,
        sa.func.ts_rank_cd(vector, query).label("rank"),
    )
    .where(vector.op("@@")(query))
    .order_by(sa.text("rank DESC"))
    .limit(20)
)

with Session(engine) as session:
    rows = session.execute(stmt).all()
```

The same idea applies to `backend.concept_synonym_name_tsvector_expression()`.

---

## Metadata Registration

If your process will use the stored sidecar columns directly, register them into the ORM metadata:

```python
from omop_alchemy.backends import resolve_backend

backend = resolve_backend(engine)
backend.register_fulltext_metadata()
```

If you later remove the columns from the database in the same process and want query helpers to fall back cleanly again:

```python
backend.unregister_fulltext_metadata()
```

This only affects SQLAlchemy metadata in the current Python process. It does not alter the database by itself.

---

## PostgreSQL Scope

This feature is PostgreSQL-specific in its database form because it relies on:

- `tsvector`
- PostgreSQL full-text query functions such as `to_tsvector` and `plainto_tsquery`
- optional GIN indexes

The helper expressions can still be imported safely, but the sidecar install / populate / drop lifecycle is only meaningful on PostgreSQL.

---

## Operational Notes:

- treat the sidecar columns as **derived search state**, not source-of-truth data
- if you bulk-load new vocabulary rows, rerun `omop-alchemy fulltext populate`
- if you use `reconcile-schema`, the sidecar columns and indexes are intentional database additions outside the core OMOP schema
- GIN indexes can be expensive to build on large vocabularies, so plan that as a real maintenance operation rather than a trivial toggle
