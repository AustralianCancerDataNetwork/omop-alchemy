from dataclasses import dataclass

"""
This module contains type definitions for the OMOP CDM model. 

These types are used to provide type hints for row types for the base directly-mapped classes in the model.
"""

@dataclass(frozen=True)
class ConceptRow:
    concept_id: int
    concept_name: str
    concept_code: str
    domain_id: str | None
    concept_class_id: str | None
    vocabulary_id: str | None
    standard_concept: str | None
