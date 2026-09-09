# Columns & Structural Mixins

OMOP Alchemy provides a small set of **column helpers and mixins** that encode recurring OMOP CDM patterns directly into ORM structure.

These utilities exist to:

- reduce boilerplate
- make OMOP semantics explicit
- keep table definitions readable
- align ORM structure with CDM specifications

They are **structural**, not analytical: they describe *how data is shaped*, not *what it means*.

---

## Column helper functions

Column helpers wrap common OMOP column patterns into small, intention-revealing factory functions.

They are thin wrappers around `sqlalchemy.orm.mapped_column` with defaults chosen to match the CDM Field-Level specifications.

### Concept foreign keys

OMOP relies heavily on concept identifiers, with specific semantics around nullability and unknown values.

::: omop_alchemy.cdm.base.column_helpers.required_concept_fk
    options:
      heading_level: 4

::: omop_alchemy.cdm.base.column_helpers.optional_concept_fk
    options:
      heading_level: 4

---

### Convenience wrappers

These helpers exist primarily for consistency and readability when defining large tables with many fields.

::: omop_alchemy.cdm.base.column_helpers.optional_fk
    options:
      heading_level: 4

::: omop_alchemy.cdm.base.column_helpers.required_int
    options:
      heading_level: 4

::: omop_alchemy.cdm.base.column_helpers.optional_int
    options:
      heading_level: 4

---

## Structural mixins

Structural mixins encode **table-level OMOP patterns** that recur across multiple CDM tables.

::: omop_alchemy.cdm.base.column_mixins.PersonScoped
    options:
      heading_level: 3

::: omop_alchemy.cdm.base.column_mixins.ConceptTyped
    options:
      heading_level: 3

::: omop_alchemy.cdm.base.column_mixins.ValueMixin
    options:
      heading_level: 3

::: omop_alchemy.cdm.base.column_mixins.DatedEvent
    options:
      heading_level: 3

::: omop_alchemy.cdm.base.column_mixins.HealthSystemContext
    options:
      heading_level: 3

::: omop_alchemy.cdm.base.column_mixins.SourceAttribution
    options:
      heading_level: 3

::: omop_alchemy.cdm.base.column_mixins.UnitConcept
    options:
      heading_level: 3

---

## Marker mixins

Some mixins exist purely to **label intent**. They do not add columns or behavior. We don't currently do anything with these.

::: omop_alchemy.cdm.base.column_mixins.FactTable
    options:
      heading_level: 3

::: omop_alchemy.cdm.base.column_mixins.ReferenceTable
    options:
      heading_level: 3
