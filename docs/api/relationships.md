# Relationships & Reference Contexts

OMOP Alchemy takes a **deliberately conservative approach to ORM relationships**.

Rather than eagerly wiring every foreign key into a bidirectional relationship, it distinguishes between:

- **structural foreign keys** (always present in tables)
- **reference lookups** (read-only joins for navigation)
- **analytical relationships** (used in views, not ETL)

This separation keeps core tables simple, predictable, and fast to load, while still enabling rich, expressive navigation when you need it.

---

## Why relationships are handled carefully

The OMOP CDM has several characteristics that make naïve ORM relationships risky:

- very long, mixed-use tables
- many optional foreign keys
- polymporphism
- frequent joins to large vocabulary tables
- mixed use cases (ETL vs analytics vs inspection)

In particular:

- ETL workflows should **not accidentally trigger joins**
- reference data should be **navigable but not mutable**
- analytical helpers should not pollute base table definitions

OMOP Alchemy addresses this by introducing **Reference Contexts**.

::: omop_alchemy.cdm.base.reference_context.ReferenceContext
    options:
      heading_level: 3

---

## The core idea

Instead of defining relationships directly on a table class, OMOP Alchemy encourages a **three-layer pattern**:

1. **Table** – structural definition only
2. **Context** – reference relationships
3. **View** – analytical behavior and validation

This keeps concerns cleanly separated.

---

## Worked example: `Person`

### 1. The base table

The core `Person` table defines:

- primary keys
- scalar fields
- foreign key columns
- *no ORM relationships*

```python

@cdm_table
class Person(CDMTableBase, Base, HealthSystemContext):
    __tablename__ = "person"

    person_id: Mapped[int] = mapped_column(primary_key=True)

    year_of_birth: Mapped[int] = required_int()
    gender_concept_id: Mapped[int] = required_concept_fk()
    race_concept_id: Mapped[int] = required_concept_fk()
    ethnicity_concept_id: Mapped[int] = required_concept_fk()

    location_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("location.location_id"),
        nullable=True,
        index=True,
    )
```

At this layer:

* the table is easy to reason about
* loading rows never triggers joins
* nothing is implicitly navigable

This is the class you want in ETL loops.

### 2. The reference context

Reference relationships are defined separately in a `Context` class:

```python

class PersonContext(ReferenceContext):
    gender = ReferenceContext._reference_relationship(
        target="Concept",
        local_fk="gender_concept_id",
        remote_pk="concept_id",
    )

    location = ReferenceContext._reference_relationship(
        target="Location",
        local_fk="location_id",
        remote_pk="location_id",
    )

```

Key properties of these relationships:

* viewonly=True
* no backrefs
* explicit join conditions
* safe to compose

### 3. The analytical view

Finally, the `PersonView` composes everything together:

```python

class PersonView(Person, PersonContext, DomainValidationMixin):
    __tablename__ = "person"
    __mapper_args__ = {"concrete": False}

    __expected_domains__ = {
        "gender_concept_id": ExpectedDomain("Gender"),
        "race_concept_id": ExpectedDomain("Race"),
    }

```

This is the class you use for:

* analytics
* cohort logic
* interactive inspection
* debugging

It is intentionally not the class you use for bulk loading.

### Why reference relationships are view-only

Reference relationships are declared as:

* `viewonly=True`
* no cascade rules
* no persistence semantics

This is intentional.

Vocabulary tables (Concept, Domain, Vocabulary, etc.) are:

* shared
* stable
* not owned by fact tables

Allowing mutation through ORM relationships would blur those boundaries and make ETL behavior harder to reason about.

### Performance considerations

Reference relationships use `lazy="selectin"`

This provides a good balance:

* avoids N+1 queries in most cases
* avoids eager joins during row loading
* keeps behaviour predictable

If you need tighter control, you can always override loading strategies in queries.