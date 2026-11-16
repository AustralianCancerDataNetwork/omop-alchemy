from datetime import date

from omop_alchemy.cdm.base import ModifierFieldConcepts
from omop_alchemy.cdm.model.clinical import Condition_OccurrenceView


def test_condition_occurrence_view_modifier_contract():
    """Condition_OccurrenceView exposes the expected modifier metadata contract."""
    cls = Condition_OccurrenceView

    assert cls.__event_id_col__ == "condition_occurrence_id"
    assert cls.__concept_id_col__ == "condition_concept_id"
    assert cls.__start_date_col__ == "condition_start_date"
    assert cls.__end_date_col__ == "condition_end_date"
    assert cls.__type_concept_id_col__ == "condition_type_concept_id"


def test_modifier_target_properties_python():
    """ModifierTargetMixin convenience properties resolve correctly in Python."""
    c = Condition_OccurrenceView(
        condition_occurrence_id=123,
        condition_concept_id=456,
        condition_start_date=date(2020, 1, 1),
        condition_end_date=date(2020, 12, 31),
        condition_type_concept_id=789,
        person_id=1,
    )

    assert c.event_id == 123
    assert c.concept_id == 456
    assert c.start_date == date(2020, 1, 1)
    assert c.end_date == date(2020, 12, 31)
    assert c.type_concept_id == 789


def test_event_id_hybrid_expression(session):
    """The event_id hybrid compiles to condition_occurrence_id in SQL."""
    q = session.query(Condition_OccurrenceView).filter(Condition_OccurrenceView.event_id == 1)

    sql = str(q.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "condition_occurrence_id" in sql


def test_modifier_target_identity():
    """Modifier target identity methods return stable OMOP metadata values."""
    cls = Condition_OccurrenceView

    assert cls.modifier_target_table() == "condition_occurrence"
    assert cls.modifier_field_concept_id() == ModifierFieldConcepts.CONDITION_OCCURRENCE
