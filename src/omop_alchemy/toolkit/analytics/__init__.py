"""Clinical logic for specific domains.

Where ``core`` and ``episodes`` are deliberately domain-agnostic, the
subpackages here encode what a value *means* in a particular clinical
context: which concepts constitute a treatment, what counts as a
significant change, how severity is graded.

``oncology``
    Cancer treatment and disease episodes, radiotherapy and systemic
    therapy dosing, and the concept sets that define them.

``body_metrics``
    Weight, height, and BMI as measurement series and trajectories.

``adverse_events``
    Severity grading against published criteria.

Each domain owns its own concept sets, kept beside the code that uses
them so that changing a domain's definition of a treatment or a threshold
touches one place.

Domains may depend on ``core`` and ``episodes``, and on each other where
the clinical logic genuinely composes — oncology cachexia grading builds
on body-metric trajectories and adverse-event criteria.
"""
