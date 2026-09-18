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
from omop_alchemy.cdm.model.flags import BooleanFlag, normalised_flag_expr, normalised_flag

@cdm_table
class Relationship(Base, ReferenceTable, CDMTableBase):
    __tablename__ = "relationship"
    __table_args__ = merge_table_args(
        omop_table_options(cluster_on=omop_primary_key_index_name("relationship")),
        {"schema": Role.VOCAB.value},
    )
    relationship_id: so.Mapped[str] = so.mapped_column(sa.String(20), primary_key=True)
    relationship_name: so.Mapped[str] = so.mapped_column(sa.String(255), nullable=False)
    is_hierarchical: so.Mapped[str] = so.mapped_column(sa.String(1), nullable=False)
    defines_ancestry: so.Mapped[str] = so.mapped_column(sa.String(1), nullable=False)
    reverse_relationship_id: so.Mapped[str] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "relationship.relationship_id")),nullable=False)
    relationship_concept_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")),nullable=False,)

    def __repr__(self):
        return f"<Relationship {self.relationship_id}>"

    @property
    def is_hierarchical_relationship(self) -> bool:
        """True only for normalised OMOP ``is_hierarchical == '1'``."""
        return normalised_flag(self.is_hierarchical) == BooleanFlag.TRUE

    @classmethod
    def is_hierarchical_relationship_expr(cls) -> sa.SQLColumnExpression[bool]:
        return sa.func.coalesce(
            normalised_flag_expr(cls.is_hierarchical) == BooleanFlag.TRUE.value,
            sa.false(),
        )

    @property
    def is_ancestry_defining(self) -> bool:
        return normalised_flag(self.defines_ancestry) == BooleanFlag.TRUE

    @classmethod
    def is_ancestry_defining_expr(cls) -> sa.SQLColumnExpression[bool]:
        return sa.func.coalesce(
            normalised_flag_expr(cls.defines_ancestry) == BooleanFlag.TRUE.value,
            sa.false(),
        )