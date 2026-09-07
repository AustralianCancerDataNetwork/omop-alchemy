# Base Tables

Base tables define the **foundation of all concrete OMOP CDM models** in OMOP Alchemy.

They establish the minimum structural and behavioral contract that distinguishes a real CDM table from:

- mixins
- views
- derived or analytical constructs

If you are authoring a new OMOP table, this is the layer you inherit from.

---

## What a “base table” means here

In OMOP Alchemy, a *base table* is:

- a concrete SQLAlchemy declarative class
- representing a real OMOP CDM table
- safe to load from CSV
- safe to serialize
- safe to validate structurally

It is **not** responsible for:

- analytics
- domain semantics
- cohort logic
- query patterns

Those concerns live elsewhere.

---

## Relationship to `orm-loader`

`CDMTableBase` builds directly on infrastructure provided by `orm-loader`.

Specifically, it inherits:

- `CSVLoadableTableInterface` — controlled CSV ingestion
- `SerialisableTableInterface` — safe object serialization

This separation is intentional:

| Layer | Responsibility |
|------|---------------|
| orm-loader | Generic ORM loading & serialization |
| OMOP Alchemy | OMOP-specific structure & semantics |

`CDMTableBase` is where those two layers meet.

---

## `CDMTableBase`

The `CDMTableBase` class is the common ancestor for all concrete OMOP CDM tables.

::: omop_alchemy.cdm.base.cdm_table_base.CDMTableBase
    options:
      show_source: true

---

## Design notes

### Abstract by default

`CDMTableBase` is declared as abstract:

```python
__abstract__ = True
```

This ensures it is never mapped on its own and is only used as a structural parent.

Concrete tables must explicitly opt in (via `@cdm_table`). The @cdm_table marker makes intent explicit:

> “This class represents a real, concrete OMOP CDM table.”

Tooling, validation, and documentation can then rely on that signal without guessing based on inheritance structure.

### Structural validation hooks

The optional class attribute `__cdm_extra_checks__: list[str]` exists to support table-specific structural validation against the OMOP CDM CSV specifications.

At present:

* it is informational
* it documents known structural constraints
* it does not trigger automatic checks

This hook exists to keep structural intent co-located with the model, even when enforcement is deferred.

### Minimal runtime helpers

CDMTableBase intentionally provides very little behavior.

The only built-in helper is `table_has_rows(session)` — a lightweight existence check
