import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role
from orm_loader.helpers import Base

from omop_alchemy.cdm.base import (
    CDMTableBase,
    cdm_table,
    merge_table_args,
    omop_index,
    role_fk,
)

@cdm_table
class Fact_Relationship(CDMTableBase, Base):
    __tablename__ = "fact_relationship"
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "domain_concept_id_1"),
        omop_index(__tablename__, "domain_concept_id_2"),
        omop_index(__tablename__, "relationship_concept_id"),
    )

    domain_concept_id_1: so.Mapped[int] = so.mapped_column(
        sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")),
        primary_key=True,
        nullable=False,
    )
    fact_id_1: so.Mapped[int] = so.mapped_column(sa.Integer, primary_key=True, nullable=False)
    domain_concept_id_2: so.Mapped[int] = so.mapped_column(
        sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")),
        primary_key=True,
        nullable=False,
    )
    fact_id_2: so.Mapped[int] = so.mapped_column(sa.Integer, primary_key=True, nullable=False)
    relationship_concept_id: so.Mapped[int] = so.mapped_column(
        sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")),
        primary_key=True,
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            "<FactRelationship "
            f"{self.domain_concept_id_1}:{self.fact_id_1} "
            f"-[{self.relationship_concept_id}]-> "
            f"{self.domain_concept_id_2}:{self.fact_id_2}>"
        )
