# Toolkit

The toolkit turns OMOP rows into objects that answer clinical questions. For example, given the ID of an oncology episode, you can inspect its treatment modality, linked drug exposures, radiotherapy dose, and weight-loss assessment while the episode remains attached to a SQLAlchemy session:

```python
from sqlalchemy.orm import Session

from omop_alchemy.toolkit.analytics.oncology import OncologyEpisode

with Session(engine) as session:
    episode = session.get(OncologyEpisode, episode_id)
    if episode is None:
        raise LookupError(f"Unknown episode: {episode_id}")

    modalities = episode.structural_modalities
    drug_exposures = episode.sact_exposures
    radiotherapy = episode.rt_dose_summary
    weight_loss = episode.critical_weight_loss_summary()
```

This is still ordinary SQLAlchemy. `OncologyEpisode` is mapped to the OMOP episode view, and properties may load related rows or resolve governed vocabulary sets through the active session. The toolkit adds interpretation and reusable retrieval rules; it does not replace the CDM models or hide when database access is required.

## Where to begin

Choose the part of the toolkit that matches the question you are asking:

| If you need to… | Start with |
|---|---|
| Resolve incoming text or source codes to OMOP concepts, compare measurements in common units, or represent events from several CDM tables consistently | [`core`](core.md) |
| Traverse episode relationships, retrieve episode-linked facts, or state how an event should be attached to an episode | [`episodes`](episodes.md) |
| Apply a clinical interpretation such as oncology modality, dose summarisation, body-metric analysis, or weight-loss grading | [`analytics`](analytics.md) |
| Check the availability and expectations of outbound data-standard integrations | [`integrations`](integrations.md) |

The dependency direction follows the same order. `episodes` can use `core`; `analytics` can use both; `integrations` can use the whole toolkit. Lower layers never import a clinical specialty or an export format. This keeps general concepts such as event identity and unit conversion independent of the analyses that use them.

## Public imports

Import from the documented area package rather than from a file beneath it:

```python
from omop_alchemy.toolkit.core.concepts import make_concept_resolver
from omop_alchemy.toolkit.analytics.oncology import OncologyEpisode
```

The area packages re-export their public API. Module names below those packages are implementation details and may change without providing a compatibility import.

!!! warning "Toolkit stability"
    Toolkit area packages are less stable than `omop_alchemy.cdm`. Treat the documented area import paths as the compatibility boundary, pin the package version in deployed applications, and review release notes before upgrading. Changes in the toolkit do not alter the CDM model API.
