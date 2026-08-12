# core

Foundational services with no clinical-domain assumptions. A concept resolver behaves
the same whether it is mapping tumour morphology or procedures; a patient timeline is
the same object whatever populates it. Domain-specific concept sets, thresholds, and
grading rules belong in [`analytics`](analytics.md), not here.

## Concept resolution

Turns a declarative description of *which* concepts belong in a lookup into a runtime
resolver that maps free text and source codes to OMOP concept IDs.

```python
from omop_alchemy.toolkit.core.concepts import make_concept_resolver

resolver = make_concept_resolver(
    session,
    name="condition lookup",
    domain_id="Condition",
)
concept_id = resolver.lookup("Adenocarcinoma of lung")
```

::: omop_alchemy.toolkit.core.concepts

## Patient timelines

Projects a person's clinical rows — conditions, measurements, drug exposures — into a
single time-ordered event stream. This has its own dedicated page, since it predates the
rest of the toolkit reorg: see [Patient Timelines](../advanced/timelines.md).

## Unit conversion

Converts measurement values to canonical units. Kilograms, pounds, centimetres, and
inches mean the same thing in every clinical domain, so the conversion rules live here
rather than with any one domain's measurement logic — see
[`analytics.body_metrics`](analytics.md#body_metrics) for where those domain-specific
measurements are resolved and normalised using these rules.

::: omop_alchemy.toolkit.core.units
