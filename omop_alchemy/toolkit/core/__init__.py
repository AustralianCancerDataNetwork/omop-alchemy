"""Foundational services shared across every clinical domain.

Core holds the parts of the toolkit that carry no assumptions about what
kind of clinical question you are asking.  A concept resolver behaves the
same whether it is mapping tumour morphology or procedures; a
patient timeline is the same object whatever populates it.

``concepts``
    Map free text and source codes to OMOP concept IDs, and hold the
    normalisation rules that make those mappings reproducible.

``timeline``
    Project heterogeneous clinical rows into a single ordered sequence of
    events for one person.

``units``
    Convert measurement values to canonical units.

Nothing in core imports from ``episodes``, ``analytics``, or
``integrations``.  Domain-specific concept sets, thresholds, and grading
rules belong with their domain under ``analytics``, not here.
"""
