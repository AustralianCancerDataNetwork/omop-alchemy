# Toolkit

`omop_alchemy.cdm` gives you the OMOP CDM schema as SQLAlchemy models. The toolkit is
what you build with them: vocabulary resolution, patient timelines, episode traversal,
domain analytics, and outbound export.

!!! warning "Experimental"
    The toolkit is newer and less battle-tested than the CDM models it sits on top of.
    Module paths below an area (anything past `toolkit.<tier>.<area>`) may still move;
    the area itself is the stable import surface. The CDM models themselves are not
    affected by anything here.

## Four tiers

Each tier may depend only on the tiers before it in this list — `core` knows nothing of
episodes or clinical domains, and nothing depends on `integrations`.

| Tier | Answers | Assumes a clinical domain? |
|---|---|---|
| [`core`](core.md) | Resolve concepts, build a patient timeline, convert units | No |
| [`episodes`](episodes.md) | Build episodes, retrieve what belongs to one | No |
| [`analytics`](analytics.md) | What does this value mean clinically? | Yes, one subpackage per domain |
| [`integrations`](integrations.md) | Export to an external data standard | No |

## A worked example

Querying an oncology episode pulls together all four tiers without you having to think
about the seams between them: concept resolution and timelines from `core`, drug
retrieval from `episodes`, oncology classification from `analytics`.

```python
from sqlalchemy.orm import Session
from omop_alchemy.toolkit.analytics.oncology import OncologyEpisode

with Session(engine) as session:
    episode = session.get(OncologyEpisode, episode_id)

    episode.structural_modality        # OncologyModality.SACT, .RADIOTHERAPY, ...
    episode.drug_exposures              # linked Drug_Exposure rows
    episode.rt_dose_summary             # RTDoseSummary, if any RT was given
    episode.critical_weight_loss_grade  # graded against Martin/CTCAE criteria
```

## Import surface

Import from the area subpackage — `omop_alchemy.toolkit.<tier>.<area>` — not from a
specific module beneath it:

```python
from omop_alchemy.toolkit.core.concepts import make_concept_resolver
from omop_alchemy.toolkit.analytics.oncology import OncologyEpisode
```

Each area's `__init__.py` re-exports its public names, and that's the part of the path
that stays stable. Files beneath it are free to move.
