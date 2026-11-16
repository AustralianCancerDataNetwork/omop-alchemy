"""Export OMOP CDM data to external standards.

Integrations convert a CDM database into the format another ecosystem
expects.  They sit at the outer edge of the toolkit: an integration may
use anything in ``core``, ``episodes``, or ``analytics``, and nothing in
those tiers depends on an integration.  Adding or changing an export
format therefore cannot affect the clinical logic beneath it.

``meds_standard``
    Export to the Medical Event Data Standard. Not yet populated.

Integrations bring their own heavyweight dependencies and each is gated
behind an optional extra, so installing omop-alchemy does not pull in
formats you are not exporting to.
"""
