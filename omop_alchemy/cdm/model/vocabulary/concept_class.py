import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role
from orm_loader.helpers import Base
from omop_alchemy.cdm.base import (
    ReferenceTable,
    cdm_table,
    CDMTableBase,
    merge_table_args,
    omop_primary_key_index_name,
    omop_table_options,
    role_fk,
)

@cdm_table
class Concept_Class(Base, ReferenceTable, CDMTableBase):
    __tablename__ = "concept_class"
    __table_args__ = merge_table_args(
        omop_table_options(cluster_on=omop_primary_key_index_name("concept_class")),
        {"schema": Role.VOCAB.value},
    )
    concept_class_id: so.Mapped[str] = so.mapped_column(sa.String(20), primary_key=True)
    concept_class_name: so.Mapped[str] = so.mapped_column(sa.String(255), nullable=False)
    concept_class_concept_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")),nullable=False,)

    def __repr__(self):
        return f"<ConceptClass {self.concept_class_id}>"
