from typing import Protocol, ClassVar, runtime_checkable, TYPE_CHECKING
from sqlalchemy.orm import DeclarativeMeta

if TYPE_CHECKING:
    from omop_alchemy.cdm.base import ExpectedDomain, DomainRule

@runtime_checkable
class HasConceptId(Protocol):
    concept_id: int

@runtime_checkable
class HasEpisodeId(Protocol):
    episode_id: int

@runtime_checkable
class HasPersonId(Protocol):
    person_id: int

@runtime_checkable
class DomainSemanticTable(Protocol):
    __tablename__: ClassVar[str]
    __mapper__: ClassVar[DeclarativeMeta]
    __expected_domains__: ClassVar[dict[str, "ExpectedDomain"]]

    @classmethod
    def collect_domain_rules(cls) -> list["DomainRule"]: ...

