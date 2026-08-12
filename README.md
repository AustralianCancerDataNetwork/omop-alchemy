# OMOP Alchemy

**OMOP Alchemy** provides a canonical, typed, SQLAlchemy-first representation of the
[OHDSI OMOP Common Data Model (CDM)](https://ohdsi.github.io/CommonDataModel/).

It is designed to support **research-ready analytics, validation, and exploration**
of OMOP data using modern Python tooling, without imposing ETL conventions or
execution-time side effects.

---

## Design goals

OMOP Alchemy is intentionally:

- **Declarative**  
  Defines tables, columns, relationships, and constraints 

- **SQLAlchemy-native**  
  Built for SQLAlchemy 2.x ORM usage

- **Safe to import anywhere**  
  No implicit engine creation, no global state, no environment assumptions.

- **Typed and inspectable**  
  Models are fully typed and introspectable for validation, tooling, and IDE support.

- **Backend-agnostic**  
  Designed to work across PostgreSQL, SQLite, and other SQLAlchemy-supported databases.

---

## What this package does *not* do

OMOP Alchemy deliberately avoids:

- Enforcing ETL conventions or data pipelines
- Auto-creating databases or loading vocabularies
- Imposing analytics frameworks or dashboards
- Making assumptions about deployment environments

These concerns are intentionally left to downstream tooling.

---

## Core features

- SQLAlchemy ORM models for OMOP CDM tables
- Explicit foreign key and relationship definitions
- Read-only *View* classes for safe navigation and analytics
- Domain validation helpers for OMOP concept integrity
- CSV loading utilities for controlled ingestion and testing
- Lightweight schema and model validation against CDM specs

---

## Example (concept navigation)

```python
from omop_alchemy.cdm.model.vocabulary.concept import ConceptView

concept = session.get(ConceptView, 320128)  # Lung cancer
concept.domain.domain_id        # "Condition"
concept.vocabulary.vocabulary_id  # "SNOMED"
concept.is_standard             # True
```

---

## Status

This project is currently beta.

The API is stabilising, but some modules may change as real-world use cases expand. Feedback and issues are welcome.

### Some additional background

This work builds on earlier research and tooling presented at the 2023 OHDSI APAC Symposium
> see [background paper](https://github.com/AustralianCancerDataNetwork/OMOP_Alchemy/blob/main/notebooks/ORMforResearchReadyData_APAC2023.pdf).

---

## Configuration

OMOP Alchemy reads all database connection and schema settings from
[oa-configurator](https://github.com/AustralianCancerDataNetwork/oa-configurator).
No `.env` files or `ENGINE` environment variables are needed.

Run once after installation:

```bash
omop-config init
omop-config configure omop_alchemy
```

See [Configuration](docs/getting-started/configuration.md) for full details.