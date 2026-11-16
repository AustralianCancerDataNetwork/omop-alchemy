"""Build episodes and traverse what belongs to them.

An OMOP ``Episode`` groups clinical events into a clinically meaningful
unit — a treatment regimen, a cycle within it, a disease course.  Two
questions follow from that, and this tier answers both:

``derivation``
    How episodes are constructed and related to one another — building
    episode queries and resolving parent/child hierarchy. Not yet
    populated; the equivalent built against materialised views lives in
    ``omop-constructs``.

``handling``
    What is inside an episode once it exists — retrieving the linked
    drug exposures, procedures, and measurements, admitting same-person
    facts within a bounded window when an explicit link is unavailable,
    and summarising what was found.

Everything here is domain-neutral.  A drug episode behaves the same
whether the drug is a cytotoxic agent or an antibiotic, so the mixins and
resolvers in this tier take concept filters and grouping keys as
parameters rather than assuming a clinical specialty.

Domain-specific episode classes compose these pieces with their own
concept sets and rules, and live with their domain — for example
``OncologyEpisode`` in ``omop_alchemy.toolkit.analytics.oncology``.
"""
