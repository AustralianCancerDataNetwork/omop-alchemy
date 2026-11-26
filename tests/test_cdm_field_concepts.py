import pytest

from omop_alchemy.cdm.base.cdm_constants import ModifierFieldConcepts


def test_modifier_field_concepts_match_semantics() -> None:
    links = pytest.importorskip("omop_semantics.links")
    from omop_semantics.runtime.default_valuesets import runtime

    alchemy_fields = {
        name.lower(): value
        for name, value in vars(ModifierFieldConcepts).items()
        if name.isupper() and isinstance(value, int)
    }
    registry_fields = runtime.modifiers.modifier_fields.exact_mapper()

    assert {f"{table}_id" for table in alchemy_fields} == set(registry_fields)
    for table, value in alchemy_fields.items():
        assert value == links.field_concept_id(table), table
