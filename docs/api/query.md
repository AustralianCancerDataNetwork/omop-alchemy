# Query Filtering

`omop_alchemy.cdm.query` provides `ConceptFilter`, a shared, reusable way to filter CDM `concept`-table queries by domain, vocabulary, concept ID, and standard/active status, with an optional row-count limit.

It exists so that downstream packages do not each need to reimplement the same filtering logic against their own copy of `Concept`'s column names. Since this package owns the `Concept` model directly, the filter can reference real columns rather than duck-typing against an opaquely imported table.

```python
from sqlalchemy import select
from omop_alchemy.cdm.model.vocabulary import Concept
from omop_alchemy.cdm.query import ConceptFilter

concept_filter = ConceptFilter(
    domains=("Condition", "Drug"),
    require_standard=True,
)
query = concept_filter.apply(select(Concept))
```

All fields are optional and combinable.

::: omop_alchemy.cdm.query.ConceptFilter
    options:
      heading_level: 3
