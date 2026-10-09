"""filter_by_keys on a colocated deployment."""

from __future__ import annotations

import sqlalchemy as sa
import sqlalchemy.orm as so

from omop_alchemy.cdm.model.clinical import Condition_Occurrence
from omop_alchemy.cdm.model.vocabulary import Concept
from omop_alchemy.cross_database import filter_by_keys


def test_filter_by_keys_is_a_subquery_on_one_database(fresh_engine: sa.Engine):
    with so.Session(fresh_engine) as session:
        predicate = filter_by_keys(
            Condition_Occurrence.condition_concept_id,
            keys_select=sa.select(Concept.concept_id),
            session=session,
        )
    assert "SELECT" in str(predicate)
