# OMOP Alchemy

OMOP Alchemy provides a **canonical, typed, SQLAlchemy-first** representation of the OHDSI OMOP Common Data Model (CDM).

It is designed for **research-ready analytics, validation, and exploration**.

## Where OMOP Alchemy fits

OMOP Alchemy sits between raw OMOP databases and exploratory analytics, validation, and research tooling

It provides:

- stable ORM models
- explicit relationships
- safe, inspectable abstractions

without imposing workflow assumptions.

---

## What’s in this documentation

### Getting started

How to install OMOP Alchemy, create sessions, and start querying safely.

- [Installation](getting-started/installation.md)
- [Quickstart](getting-started/quickstart.md)
- [Maintenance CLI](getting-started/maintenance.md)

---

### OMOP CDM models

Typed ORM models corresponding to the official OMOP CDM tables,
organised by clinical and operational domain.

- [CDM overview](models/index.md)
- [Clinical models](models/clinical/index.md)
- [Health system models](models/health_system/index.md)
- [Vocabulary & concepts](models/vocabulary/index.md)
- [Derived models](models/derived/index.md)
- [Structural tables](models/structural/index.md)
- [Unstructured data](models/unstructured/index.md)
- [Health economic data](models/health_economic/index.md)
- [Metadata](models/metadata/index.md)

---

### Validation & semantics

Object- and model-level validation utilities that help maintain semantic clarity during analysis.

- [Validation overview](validation/index.md)
- [Domain rules](validation/domain-rules.md)
- [Runtime domain checking](validation/domain-runtime.md)

---

### Advanced usage

Patterns and techniques for more complex analytical work.

- [Timelines & longitudinal analysis](advanced/timelines.md)
- [Backend considerations](advanced/backends.md)
- [PostgreSQL Full-Text Search](advanced/fulltext.md)

---

### API reference

Low-level primitives and decorators used throughout the library.

- [Base classes](api/base.md)
- [Columns & mixins](api/columns.md)
- [Typing helpers](api/typing.md)


## Example

```python
from omop_alchemy.cdm.model.vocabulary import ConceptView

concept = session.get(ConceptView, 320128)
concept.domain.domain_id
concept.vocabulary.vocabulary_id
concept.is_standard
```

### Status

OMOP Alchemy is beta. Toolkit APIs are experimental and carry no compatibility guarantees; feedback is welcome.
