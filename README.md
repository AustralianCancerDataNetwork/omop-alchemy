# OMOP Alchemy

**OMOP Alchemy** provides a canonical, typed, SQLAlchemy-first representation of the [OHDSI OMOP Common Data Model (CDM)](https://ohdsi.github.io/CommonDataModel/).

It is designed to support fluency for **research-ready analytics, validation, and exploration** of OMOP data using modern Python tooling, without imposing ETL conventions or execution-time side effects.

---

## Design goals

OMOP Alchemy is intentionally:

- **Declarative**  
  Defines tables, columns, relationships, and constraints 

- **Typed and inspectable**  
  Models are fully typed and introspectable for validation, tooling, and IDE support.

- **Backend-agnostic**  
  Layered abstractions for adding in new supported backend behaviours.

---

## Core features

- SQLAlchemy ORM models for OMOP CDM tables
- Explicit foreign key and relationship definitions
- Lightweight mapper versions to provide simple model validation against CDM for use in ETL loops without side-effects or performance hit that can come from relationship instantiation within the runtime
- Read-only *View* classes for safe navigation and analytics that include complex multi-table objects such as conditions with their modifiers, episodes with their events
- Domain validation helpers for OMOP concept integrity

---

## Example (concept navigation)

```python
from omop_alchemy.cdm.model.vocabulary.concept import ConceptView

concept = session.get(ConceptView, 320128)  # Lung cancer
concept.domain.domain_id                    # "Condition"
concept.vocabulary.vocabulary_id            # "SNOMED"
concept.is_standard                         # True
```

---

## Status

The core API under `cdm/` should be considered stable as of the 1.x release.

The toolkit API is stabilising, but some modules may change as real-world use cases expand. Feedback and issues are welcome.

### Some additional background

This work builds on earlier research and tooling presented at the 2023 OHDSI APAC Symposium
> see [background paper](https://github.com/AustralianCancerDataNetwork/OMOP_Alchemy/blob/main/notebooks/ORMforResearchReadyData_APAC2023.pdf).

---

## Configuration

OMOP Alchemy reads all database connection and schema settings from [oa-configurator](https://github.com/AustralianCancerDataNetwork/oa-configurator). No `.env` files or `ENGINE` environment variables are needed.

Run once after installation:

```bash
omop-config init
omop-config configure omop_alchemy
```

See [Configuration](docs/getting-started/configuration.md) for full details.