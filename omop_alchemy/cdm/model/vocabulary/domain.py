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
class Domain(Base, ReferenceTable, CDMTableBase):
    __tablename__ = "domain"
    __table_args__ = merge_table_args(
        omop_table_options(cluster_on=omop_primary_key_index_name("domain")),
        {"schema": Role.VOCAB.value},
    )
    domain_id: so.Mapped[str] = so.mapped_column(sa.String(20), primary_key=True)
    domain_name: so.Mapped[str] = so.mapped_column(sa.String(255), nullable=False)
    # use_alter=True breaks the cyclical FK at creation time, and instead defers it to a
    # separate ALTER TABLE ADD CONSTRAINT statement that is handled differently per dialect
    domain_concept_id: so.Mapped[int] = so.mapped_column(
        sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id"), use_alter=True), nullable=False,
    )

    def __repr__(self):
        return f'<Domain {self.domain_id} - {self.domain_name}>'
