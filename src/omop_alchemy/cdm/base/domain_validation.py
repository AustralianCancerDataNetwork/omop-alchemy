from sqlalchemy import orm as so
from orm_loader.helpers import get_model_by_tablename
#from .domain_rule import DomainRule
from typing import FrozenSet
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class DomainRule:

    """
    *DomainRule*
    
    Immutable specification of an expected OMOP domain constraint.

    A DomainRule describes the semantic expectation that a given
    concept ID field on a table or view should reference concepts
    from one or more OMOP domains.

    Domain rules are derived from model declarations and are intended
    for inspection, documentation, and validation workflows.
    They do not enforce behavior or mutate data.

    Parameters
    ----------
    table : str
        Name of the OMOP table or view the rule applies to.
    field : str
        Name of the concept ID field being constrained.
    allowed_domains : FrozenSet[str]
        Set of OMOP domains that are considered valid.
    allowed_classes : Optional[set[str]]
        Optional restriction to specific concept classes.
    """
    table: str
    field: str
    allowed_domains: FrozenSet[str]
    allowed_classes: Optional[set[str]] = None


@dataclass(frozen=True)
class ExpectedDomain:
    """
    *ExpectedDomain*

    Declares one or more expected OMOP domains for a concept field.

    ExpectedDomain is used on View classes to express semantic intent.


    Examples
    --------
    >>> ExpectedDomain("Gender").domains
    frozenset({'Gender'})
    >>> ExpectedDomain("Race", "Ethnicity").domains
    frozenset({'Race', 'Ethnicity'})
    """
    domains: FrozenSet[str]

    def __init__(self, *domains: str):
        object.__setattr__(self, "domains", frozenset(domains))


class DomainValidationMixin:
    """
    *DomainValidationMixin*

    Adds lightweight runtime domain validation to OMOP View classes.

    This mixin enables best-effort semantic checks that verify whether
    referenced concept IDs belong to expected OMOP domains.

    Validation is advisory:

    - no exceptions are raised
    - no data is mutated
    - detached objects are handled safely

    Intended for *View classes only*.

    Examples
    --------
    Specification on a View:

    >>> class PersonView(Person, DomainValidationMixin):
    >>>    __expected_domains__ = {
    >>>        "gender_concept_id": ExpectedDomain("Gender"),
    >>>        "race_concept_id": ExpectedDomain("Race"),
    >>>        "ethnicity_concept_id": ExpectedDomain("Ethnicity"),
    >>>    }

    Runtime usage:

    >>> p = session.get(PersonView, 123)
    >>> p.is_domain_valid
    True

    Violations can be inspected without raising exceptions:
    >>> p.domain_violations
    ["gender_concept_id not in domain(s): ['Gender']"]
    """
    __expected_domains__: dict[str, ExpectedDomain] = {}

    @classmethod
    def collect_domain_rules(cls) -> list[DomainRule]:
        """
        Collect declared domain expectations as canonical DomainRule objects.

        Returns
        -------
        list[DomainRule]
            Domain rules derived from ``__expected_domains__``.
        """
        rules: list[DomainRule] = []
        
        if not hasattr(cls, "__tablename__"):
            raise TypeError(
                f"{cls.__name__} defines domain rules but is not a mapped table"
            )
        
        for field, spec in cls.__expected_domains__.items():
            rules.append(
                DomainRule(
                    table=cls.__tablename__, # ty: ignore[invalid-argument-type]
                    field=field,
                    allowed_domains=spec.domains,
                )
            )
        return rules

    def _check_domain(self, field: str) -> bool:
        """
        Check whether a single concept field satisfies its domain expectation.

        Returns True if:
        - no expectation is declared
        - concept_id is 0
        - object is detached
        - concept domain matches expectation
        """
        expected = self.__expected_domains__.get(field)
        if not expected:
            return True

        concept_id = getattr(self, field)
        if concept_id == 0:
            return True  # OMOP allows 0

        session = so.object_session(self)
        if session is None:
            return True  # detached; best-effort
        # need to be able to query concept table but can't import directly here to avoid circular imports
        ConceptCls = get_model_by_tablename("Concept")
        if ConceptCls is None:
            return False
        concept = session.get(ConceptCls, concept_id)
        return concept.domain_id in expected.domains if concept else False  # type: ignore[union-attr]


    @property
    def domain_violations(self) -> list[str]:
        """
        Human-readable descriptions of domain violations on this object.
        """
        issues = []
        for field, expected in self.__expected_domains__.items():
            if not self._check_domain(field):
                issues.append(
                    f"{field} not in domain(s): {sorted(expected.domains)}"
                )
        return issues

    @property
    def is_domain_valid(self) -> bool:
        """
        Whether this object satisfies all declared domain expectations.
        """
        return not self.domain_violations

