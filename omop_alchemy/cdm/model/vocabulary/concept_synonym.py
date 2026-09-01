import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role
from orm_loader.helpers import Base
from omop_alchemy.cdm.base import (
    ReferenceTable,
    cdm_table,
    CDMTableBase,
    merge_table_args,
    omop_index,
    role_fk,
)

@cdm_table
class Concept_Synonym(Base, ReferenceTable, CDMTableBase):
    __tablename__ = "concept_synonym"
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "concept_id", cluster=True),
        # Has to be wrapped in func.lower() as that is the common query
        # as it prevents captialisation mismatches between query and data.
        omop_index(
            __tablename__,
            sa.func.lower(sa.column("concept_synonym_name")),
            name="ix_concept_synonym_concept_synonym_name_lower",
        ),
        {"schema": Role.VOCAB.value},
    )
    concept_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")),primary_key=True)
    concept_synonym_name: so.Mapped[str] = so.mapped_column(sa.String(1000),primary_key=True)
    language_concept_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")),primary_key=True)
