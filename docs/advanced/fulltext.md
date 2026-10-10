# PostgreSQL Full-Text Search

OMOP Alchemy includes an **optional** PostgreSQL full-text search integration for selected vocabulary text fields.

This feature is deliberately bolt-on:

- OMOP_Alchemy works without the full-text search feature (i.e. optional)
- the ORM models never carry the extra database columns

This is useful when you want faster repeated text-search workloads on PostgreSQL, without forcing every environment to carry extra full-text infrastructure.

---

## What It Covers

The current full-text support targets:

- `concept.concept_name`
- `concept_synonym.concept_synonym_name`

The stored column of a target table is looked up on the resolved `Backend` instance:

```python
from omop_alchemy.backends import resolve_backend
from omop_alchemy.cdm.model.vocabulary import Concept, Concept_Synonym

backend = resolve_backend(engine)
backend.fulltext_vector_column(engine, Concept.__table__)
backend.fulltext_vector_column(engine, Concept_Synonym.__table__)
```

`fulltext_vector_column()` checks that the column exists in the database behind `engine` and raises `FullTextError` otherwise. The returned column is bound to its table, so it is schema-qualified and follows the engine's schema translation, but it is never attached to the ORM table: `Concept.__table__` is identical in every process, whether or not full-text search is installed anywhere.

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

That is enough to activate the feature. The rest of this page explains when to use it and how to operate it safely.

## When To Use It

Use the optional full-text feature when:

- you are on PostgreSQL
- you run repeated vocabulary-name or synonym-name search workloads

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

Many users only need occasional text matching. Others want fast repeated full-text lookups across large vocabularies and are happy to manage the extra schema objects.

OMOP Alchemy therefore treats full-text sidecars as an **optional PostgreSQL enhancement**, not as part of the core required OMOP schema.

---

## Stored Columns

Full-text search uses real `tsvector` columns in the database, with optional GIN indexes. `backend.fulltext_vector_column()` returns the stored column; it raises `FullTextError` when the column is not installed.

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
vector = backend.fulltext_vector_column(engine, Concept.__table__)
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

The same applies to `Concept_Synonym.__table__`.

---

## ORM Boundary

The stored column is usable in `WHERE` and `ORDER BY` expressions only. The ORM does not know it: `Concept` has no `concept_name_tsvector` attribute, and loading `Concept` entities never selects it. Installing full-text search in one database therefore never affects loads into another database or schema in the same process.

---

## PostgreSQL Scope

This feature is PostgreSQL-specific in its database form because it relies on:

- `tsvector`
- PostgreSQL full-text query functions such as `to_tsvector` and `plainto_tsquery`
- optional GIN indexes

On other backends `fulltext_vector_column()` raises `FeatureNotSupportedError`, and the sidecar install / populate / drop lifecycle is only meaningful on PostgreSQL.

---

## Operational Notes:

- treat the sidecar columns as **derived search state**, not source-of-truth data
- if you bulk-load new vocabulary rows, rerun `omop-alchemy fulltext populate`
- if you use `reconcile-schema`, the sidecar columns and indexes are intentional database additions outside the core OMOP schema
- GIN indexes can be expensive to build on large vocabularies, so plan that as a real maintenance operation rather than a trivial toggle
