"""Convert measurement values to canonical units.

Measurement rows carry whatever unit the source system recorded.  Before
values can be compared, trended, or fed into a calculation they need to
agree on a unit, and that conversion must be explicit about what it will
and will not accept.

``BodyUnitConversionRules`` converts anthropometric measurements to
kilograms and centimetres, driven by the unit concept on each row rather
than by guesswork.  ``default_body_unit_conversion_rules`` supplies the
standard rule set; ``INCH_TO_CM`` and ``LB_US_TO_KG`` are the underlying
factors where a caller needs them directly.

A reading whose unit concept is unrecognised is not converted and not
silently passed through — callers are told the value could not be
normalised, so an unconvertible unit never reaches a calculation
disguised as a valid one.
"""

from .body_units import (
    INCH_TO_CM,
    LB_US_TO_KG,
    BodySizeUnitConcepts,
    BodyUnitConversionRules,
    default_body_size_unit_concepts,
    default_body_unit_conversion_rules,
)

__all__ = [
    "INCH_TO_CM",
    "LB_US_TO_KG",
    "BodySizeUnitConcepts",
    "BodyUnitConversionRules",
    "default_body_size_unit_concepts",
    "default_body_unit_conversion_rules",
]
