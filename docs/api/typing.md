# Typing

OMOP Alchemy exposes a set of **Protocols and typed containers** for code that needs to
interact with CDM classes without coupling to specific ORM implementations.

These live in two modules:

| Module | Contents |
|--------|---------|
| `omop_alchemy.cdm.base.typing` | Runtime-checkable Protocols for structural checking |
| `omop_alchemy.cdm.model.typing` | Typed row containers |

---

## Protocols (`cdm.base.typing`)

### `HasConceptId`

Satisfied by any object with an integer `concept_id` attribute.

::: omop_alchemy.cdm.base.typing.HasConceptId
    options:
        heading_level: 4
        show_root_heading: false
        members: true

---

### `HasPersonId`

Satisfied by any object with an integer `person_id` attribute.

::: omop_alchemy.cdm.base.typing.HasPersonId
    options:
        heading_level: 4
        show_root_heading: false
        members: true

---

### `HasEpisodeId`

Satisfied by any object with an integer `episode_id` attribute.

::: omop_alchemy.cdm.base.typing.HasEpisodeId
    options:
        heading_level: 4
        show_root_heading: false
        members: true

---

### `DomainSemanticTable`

Structural protocol for CDM ORM classes that participate in domain validation. A class
satisfies this protocol if it has `__tablename__`, `__mapper__`, `__expected_domains__`,
and a `collect_domain_rules()` classmethod.

::: omop_alchemy.cdm.base.typing.DomainSemanticTable
    options:
        heading_level: 4
        show_root_heading: false
        members: true

---

## Typed row containers (`cdm.model.typing`)

### `ConceptRow`

A frozen dataclass representing the core fields of a concept lookup row. Used where a
lightweight, hashable concept record is preferable to a full ORM object.

::: omop_alchemy.cdm.model.typing.ConceptRow
    options:
        heading_level: 4
        show_root_heading: false
        members: true
