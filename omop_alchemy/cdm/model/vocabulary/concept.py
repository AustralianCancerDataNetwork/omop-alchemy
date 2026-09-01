import sqlalchemy as sa
import sqlalchemy.orm as so
from sqlalchemy.ext.declarative import declared_attr
from typing import Optional, TYPE_CHECKING, List
from datetime import date
if TYPE_CHECKING:
    from .domain import Domain
    from .vocabulary import Vocabulary
    from .concept_class import Concept_Class
    from .concept_ancestor import Concept_Ancestor
    from .concept_relationship import Concept_Relationship

from oa_configurator import Role
from orm_loader.helpers import Base
from omop_alchemy.cdm.base import (
    ReferenceTable,
    cdm_table,
    CDMTableBase,
    ReferenceContext,
    merge_table_args,
    omop_index,
    omop_primary_key_index_name,
    omop_table_options,
    role_fk,
)
from omop_alchemy.cdm.model.flags import (
    StandardConceptFlag,
    InvalidReasonMixin,
    normalised_flag_expr,
    normalised_flag,
)

@cdm_table
class Concept(
    ReferenceTable,
    CDMTableBase,
    InvalidReasonMixin,
    Base
):
    __tablename__ = "concept"
    __table_args__ = merge_table_args(
        omop_index(__tablename__, "concept_code"),
        omop_index(__tablename__, "vocabulary_id"),
        omop_index(__tablename__, "domain_id"),
        omop_index(__tablename__, "concept_class_id"),
        # Has to be wrapped in func.lower() as that is the common query
        # as it prevents captialisation mismatches between query and data.
        omop_index(
            __tablename__,
            sa.func.lower(sa.column("concept_name")),
            name="ix_concept_concept_name_lower",
        ),
        omop_table_options(cluster_on=omop_primary_key_index_name("concept")),
        {"schema": Role.VOCAB.value},
    )
    concept_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    concept_name: so.Mapped[str] = so.mapped_column(sa.String(255), nullable=False)
    domain_id: so.Mapped[str] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "domain.domain_id")), nullable=False)
    vocabulary_id: so.Mapped[str] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "vocabulary.vocabulary_id")), nullable=False)
    concept_class_id: so.Mapped[str] = so.mapped_column(sa.ForeignKey(role_fk(Role.VOCAB, "concept_class.concept_class_id")), nullable=False)
    standard_concept: so.Mapped[Optional[str]] = so.mapped_column(sa.String(1), nullable=True)
    concept_code: so.Mapped[str] = so.mapped_column(sa.String(50), nullable=False)
    valid_start_date: so.Mapped[date] = so.mapped_column(sa.Date(), nullable=False)
    valid_end_date: so.Mapped[date] = so.mapped_column(sa.Date(), nullable=False)

    @property
    def is_standard(self) -> bool:
        """True only for normalised OMOP ``standard_concept == 'S'``."""
        return normalised_flag(self.standard_concept) == StandardConceptFlag.STANDARD

    @classmethod
    def is_standard_expr(cls) -> sa.SQLColumnExpression[bool]:
        """SQL-side counterpart to :attr:`is_standard`. 
        
        Note that we use this somewhat goofy expression here and in similarly 
        constructed flag-derived filters so that we can ensure that this never 
        evaluates to NULL, which is important for filtering in queries, being 
        able to robustly `or_` it with other expressions, query negation etc. 
        
        The `coalesce` ensures that if the `standard_concept` is NULL, it will 
        return `False` instead of NULL.
        """
        return sa.func.coalesce(
            normalised_flag_expr(cls.standard_concept) == StandardConceptFlag.STANDARD.value,
            sa.false(),
        )

    @property
    def is_classification(self) -> bool:
        """True only for normalised OMOP ``standard_concept == 'C'`` — a classification
        node, valid for hierarchy navigation but not as a mapping target."""
        return normalised_flag(self.standard_concept) == StandardConceptFlag.CLASSIFICATION

    @classmethod
    def is_classification_expr(cls) -> sa.SQLColumnExpression[bool]:
        """SQL-side counterpart to :attr:`is_classification`."""
        return sa.func.coalesce(
            normalised_flag_expr(cls.standard_concept) == StandardConceptFlag.CLASSIFICATION.value,
            sa.false(),
        )

class ConceptContext(ReferenceContext):
    """
    Navigational relationships for Concept.

    This mixin defines read-only ORM relationships that resolve
    foreign keys into reference tables and hierarchy navigation.
    """
    
    domain: so.Mapped["Domain"] = ReferenceContext._reference_relationship(target="Domain",local_fk="domain_id",remote_pk="domain_id") # type: ignore[assignment]
    vocabulary: so.Mapped["Vocabulary"] = ReferenceContext._reference_relationship(target="Vocabulary",local_fk="vocabulary_id",remote_pk="vocabulary_id") # type: ignore[assignment]
    concept_class: so.Mapped["Concept_Class"] = ReferenceContext._reference_relationship(target="Concept_Class",local_fk="concept_class_id",remote_pk="concept_class_id") # type: ignore[assignment]

    @declared_attr
    def outgoing_relationships(cls) -> so.Mapped[List["Concept_Relationship"]]:
        return so.relationship(
            "Concept_Relationship",
            primaryjoin=f"{cls.__name__}.concept_id == Concept_Relationship.concept_id_1", # type: ignore
            foreign_keys="Concept_Relationship.concept_id_1",
            viewonly=True,
            lazy="select",
        )

    @declared_attr
    def incoming_relationships(cls) -> so.Mapped[List["Concept_Relationship"]]:
        return so.relationship(
            "Concept_Relationship",
            primaryjoin=f"{cls.__name__}.concept_id == Concept_Relationship.concept_id_2", # type: ignore
            foreign_keys="Concept_Relationship.concept_id_2",
            viewonly=True,
            lazy="select",
        )

    @declared_attr
    def ancestors(cls) -> so.Mapped[List["Concept_Ancestor"]]:
        return so.relationship(
            "Concept_Ancestor",
            primaryjoin=f"{cls.__name__}.concept_id == Concept_Ancestor.descendant_concept_id", # type: ignore
            foreign_keys="Concept_Ancestor.descendant_concept_id",
            viewonly=True,
            lazy="select",
        )

    @declared_attr
    def descendants(cls) -> so.Mapped[List["Concept_Ancestor"]]:
        return so.relationship(
            "Concept_Ancestor",
            primaryjoin=f"{cls.__name__}.concept_id == Concept_Ancestor.ancestor_concept_id", # type: ignore
            foreign_keys="Concept_Ancestor.ancestor_concept_id",
            viewonly=True,
            lazy="select",
        )

class ConceptView(Concept, ConceptContext):
    """
    Rich, navigable Concept mapping.

    Use when:
    - traversing vocabulary relationships
    - exploring hierarchies
    - semantic inspection

    Avoid in tight loops or ETL paths.
    """
    __tablename__ = "concept"
    # Must match Concept.__table_args__'s schema exactly: same (schema, name)
    # key is what makes SQLAlchemy reuse Concept's own Table object here
    # instead of building a second, distinct one with no FK link between them.
    __table_args__ = {"schema": Role.VOCAB.value}
    __mapper_args__ = {"concrete": False}
