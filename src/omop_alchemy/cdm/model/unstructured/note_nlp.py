import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role
from typing import Optional
from datetime import date

from orm_loader.helpers import Base

from omop_alchemy.cdm.base import (
    role_fk,
    CDMTableBase,
    cdm_table, 
    optional_concept_fk,
    merge_table_args,
    omop_index,
)


@cdm_table
class Note_NLP(CDMTableBase, Base):
    __tablename__ = "note_nlp"
    __table_args__ = merge_table_args(
        {"schema": Role.PRIMARY.value},
        omop_index(__tablename__, "note_id", cluster=True),
        omop_index(__tablename__, "note_nlp_concept_id"),
    )

    note_nlp_id: so.Mapped[int] = so.mapped_column(primary_key=True)

    note_id: so.Mapped[int] = so.mapped_column(
        sa.ForeignKey(role_fk(Role.PRIMARY, "note.note_id")),
        nullable=False,
    )

    section_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()

    snippet: so.Mapped[Optional[str]] = so.mapped_column(
        sa.String(250),
        nullable=True,
    )

    offset: so.Mapped[Optional[str]] = so.mapped_column(
        sa.String(50),
        nullable=True,
    )

    lexical_variant: so.Mapped[str] = so.mapped_column(
        sa.String(250),
        nullable=False,
    )

    note_nlp_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    note_nlp_source_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()

    nlp_system: so.Mapped[Optional[str]] = so.mapped_column(
        sa.String(250),
        nullable=True,
    )

    nlp_date: so.Mapped[date] = so.mapped_column(sa.Date, nullable=False)
    nlp_datetime: so.Mapped[Optional[date]] = so.mapped_column(
        sa.DateTime,
        nullable=True,
    )

    term_exists: so.Mapped[Optional[str]] = so.mapped_column(
        sa.String(1),
        nullable=True,
    )

    term_temporal: so.Mapped[Optional[str]] = so.mapped_column(
        sa.String(50),
        nullable=True,
    )

    term_modifiers: so.Mapped[Optional[str]] = so.mapped_column(
        sa.String(2000),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<NoteNLP {self.note_nlp_id}>"
