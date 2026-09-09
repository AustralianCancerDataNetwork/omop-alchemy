# API Reference

This section documents the **core authoring primitives** used to define OMOP CDM models in OMOP Alchemy.

These APIs are intentionally **low-level, explicit, and composable**. They are designed for *model authors*, not end-users or analysts.

If you are:

- defining new OMOP CDM tables or views
- extending the model surface
- encoding structural or semantic conventions
- building derived or analytical layers

…this is the layer you will work with most directly.


| Section | Purpose |
|------|--------|
| Base tables | Core base classes and inheritance structure |
| Columns | Reusable column helpers and FK patterns |
| Mixins | Structural mixins encoding OMOP table semantics |
| Typing | Protocols for static typing and semantic contracts |


The APIs documented here follow a few consistent principles:

- **No hidden behavior**  
  Nothing creates engines, sessions, or tables implicitly.

- **Declarative first**  
  Structure and intent should be visible directly on the model class.

- **Structural, not analytical**  
  These tools encode *what a table is*, not *how it is used*.

- **Safe to import anywhere**  
  All modules are import-safe and side-effect free.

This makes the API suitable for:

- schema inspection
- validation tooling
- static analysis
- documentation generation
- interactive exploration

---

## Architecture

Layered architecture specification is described in [Architecture](./architecture.md)

## Base table infrastructure

At the core of OMOP Alchemy is a small number of base classes that define what it means to be a CDM table.

These classes integrate with lower-level infrastructure (i.e. `orm-loader`) but remain OMOP-specific.

**[Base tables](base.md)**


---

## Column helpers

OMOP CDM tables repeat a number of column patterns: concept foreign keys, nullable vs required fields, source attribution, etc.

Column helpers provide **named, intention-revealing shortcuts** for these patterns.

**[Column helpers](columns.md)**

---

## Structural mixins

Many OMOP tables share structural semantics for example:

- person-scoped records
- dated events
- value-typed observations
- unit concepts
- health system attribution
- accepted modifiable targets / sources

Don't define this behaviour directly unless it is truly a new pattern - instead you should use the available mixins, which encode these patterns once, and make them reusable and inspectable.

**[Column mixins](columns.md)**

---

## Typing and semantic contracts

OMOP Alchemy makes heavy use of Python typing to express *semantic expectations*:

- “this object has a concept_id”
- “this table participates in domain validation”
- “this is a clinical event”

These protocols support:

- static type checking
- IDE assistance
- tooling and validation layers

**[Typing protocols](typing.md)**

---

## Relationship to other layers

This API layer sits *above* generic ORM infrastructure and *below* analytical or validation tooling.

| Layer | Responsibility |
|------|---------------|
| Database | Physical storage, constraints |
| orm-loader | Generic table loading, serialization |
| **OMOP Alchemy API** | **OMOP-specific structure & semantics** |
| **OMOP Alchemy Toolkit** | **Standard, generalisable queries, composition, exploration** |
| Validation | Domain and semantic checks |