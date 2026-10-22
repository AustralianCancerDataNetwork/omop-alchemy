from enum import StrEnum, nonmember
import sqlalchemy as sa
import sqlalchemy.orm as so
from typing import Optional

class StandardConceptFlag(StrEnum):
    """Allowed non-null values of ``concept.standard_concept`` (OMOP CDM v5.4)."""

    STANDARD = "S"
    CLASSIFICATION = "C"

    # Keep the complete allowed-value set available to callers that need to
    # validate the column independently of the standard/classification split.
    values = nonmember(frozenset({STANDARD, CLASSIFICATION}))

class InvalidReasonFlag(StrEnum):
    """Allowed non-null values of ``concept.invalid_reason`` (OMOP CDM v5.4)."""

    DELETED = "D"
    UPDATED = "U"

class BooleanFlag(StrEnum):
    """Allowed non-null values of ``relationship.is_hierarchical`` and 
    ``relationship.defines_ancestry`` (OMOP CDM v5.4)."""
    TRUE = "1"
    FALSE = "0"

def normalised_flag_expr(
    column: sa.SQLColumnExpression[Optional[str]],
) -> sa.SQLColumnExpression[Optional[str]]:
    """Return a canonical OMOP flag expression.

    OMOP CDM v5.4 allows only ``NULL``/``'S'``/``'C'`` for ``standard_concept``
    and ``NULL``/``'D'``/``'U'`` for ``invalid_reason``. Some real-world loads
    contain blank or whitespace-only strings instead of ``NULL``; those are
    normalised here defensively so callers do not need to reimplement the same
    tolerance logic.

    Non-empty non-canonical values are left unchanged so downstream validation
    can still detect them as bad data rather than silently treating them as a
    valid state.
    """
    return sa.func.nullif(sa.func.trim(column), "")

def normalised_flag(value: str | None) -> str | None:
    """Python counterpart to :func:`normalised_flag_expr`.

    Blank and whitespace-only strings normalise to ``None``; non-empty
    non-canonical values are returned unchanged so callers can still detect
    them as bad data.
    """
    if value is None:
        return None
    return value.strip() or None


class InvalidReasonMixin:
    invalid_reason: so.Mapped[Optional[str]] = so.mapped_column(sa.String(1), nullable=True)

    @property
    def is_valid(self) -> bool:
        """True when ``invalid_reason`` is unset (NULL, blank, or whitespace-only)."""
        return normalised_flag(self.invalid_reason) is None

    @classmethod
    def is_valid_expr(cls) -> sa.SQLColumnExpression[bool]:
        """SQL-side counterpart to :attr:`is_valid`."""
        return normalised_flag_expr(cls.invalid_reason).is_(None)