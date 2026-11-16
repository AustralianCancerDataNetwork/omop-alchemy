"""Clinical tooling built on top of the OMOP CDM models.

``omop_alchemy.cdm`` gives you the CDM schema as SQLAlchemy models.  The
toolkit is what you build with them: vocabulary resolution, patient
timelines, episode traversal, domain analytics, and outbound export.

The toolkit is organised into four tiers:

``core``
    Foundational services with no clinical-domain assumptions — concept
    resolution, patient timelines, unit conversion.

``episodes``
    Domain-neutral machinery for building episodes and traversing what
    they contain.

``analytics``
    Clinical-domain logic, one subpackage per domain — oncology, body
    metrics, adverse events.

``integrations``
    Outbound export to external data standards.

Each tier depends only on the tiers above it in that list.  ``core`` knows
nothing of episodes or clinical domains; ``integrations`` may use anything
but is used by nothing.

Import from the area subpackages, which are the supported surface::

    from omop_alchemy.toolkit.core.concepts import make_concept_resolver
    from omop_alchemy.toolkit.analytics.oncology import OncologyEpisode

Individual module paths beneath an area are internal and may be
reorganised; the area subpackage always re-exports the public names.
"""
