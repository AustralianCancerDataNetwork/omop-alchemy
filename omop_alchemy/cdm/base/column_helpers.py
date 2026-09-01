from typing import Any
import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role


def role_fk(role: Role, target: str) -> str:
    """Schema-qualify an FK target string with a schema role placeholder.

    FK string resolution happens lazily, at mapper-configuration time, so
    the target table's own schema role can't be looked up dynamically
    without an import-order dependency on whichever file declares it;
    role must be given explicitly, matching what the target table
    declares in its own __table_args__.

    Parameters
    ----------
    role : Role
        Schema role the target table is tagged with.
    target : str
        Unqualified "table.column" FK target string.

    Returns
    -------
    str
        Schema-qualified FK target string.
    """
    return f"{role.value}.{target}"


def required_concept_fk():
    """
    *required_concept_fk*

    OMOP-required concept foreign key.

    This pattern is used when the CDM requires a concept reference,
    but allows an explicit “unknown” value (`concept_id = 0`).

    Semantics:

    - Must exist
    - Unknown allowed (concept_id = 0)
    - Matches CDM Field-Level spec
    - foreign key to `concept.concept_id`, always in the Role.VOCAB schema

    To index this column, add an explicit `omop_index(...)` to the
    model's `__table_args__` rather than indexing the column directly —
    see `omop_alchemy.cdm.base.indexing`.

    """
    return so.mapped_column(
        sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")),
        nullable=False,
        default=0,
    )

def optional_concept_fk(**kwargs: Any):
    """
    *optional_concept_fk*

    Used when a concept reference is genuinely optional.

    foreign key to `concept.concept_id`, always in the Role.VOCAB schema.

    To index this column, add an explicit `omop_index(...)` to the
    model's `__table_args__` rather than indexing the column directly —
    see `omop_alchemy.cdm.base.indexing`.

    """
    return so.mapped_column(
        sa.ForeignKey(role_fk(Role.VOCAB, "concept.concept_id")),
        nullable=True,
        **kwargs,
    )

def optional_fk(target: str):
    """
    *optional_fk*

    Optional foreign keys to non-concept tables.

    target must already be schema-qualified (see role_fk()) if it points
    into a Role-tagged table.

    To index this column, add an explicit `omop_index(...)` to the
    model's `__table_args__` rather than indexing the column directly —
    see `omop_alchemy.cdm.base.indexing`.

    """
    return so.mapped_column(
        sa.ForeignKey(target),
        nullable=True,
    )

def required_int():
    """
    *required_int*

    Required integer column.
    """
    return so.mapped_column(sa.Integer, nullable=False)

def optional_int():
    """
    *optional_int*

    Optional integer column.
    """
    return so.mapped_column(sa.Integer, nullable=True)
