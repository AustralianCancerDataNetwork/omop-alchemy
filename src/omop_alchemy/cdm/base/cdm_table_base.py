from orm_loader.tables import CSVLoadableTableInterface, SerialisableTableInterface
import sqlalchemy as sa
import sqlalchemy.orm as so

class CDMTableBase(CSVLoadableTableInterface, SerialisableTableInterface):  
    """
    Base class for CDM tables that support CSV loading and validation.
    """
    __abstract__ = True
    __omop_is_cdm_table__ = False
    __omop_table_category__: str | None = None

    """
    Adds structural validation of ORM models against
    the official OMOP CDM CSV specifications.
    """
    __cdm_extra_checks__: list[str] = []

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        cls.__omop_is_cdm_table__ = False
        cls.__omop_table_category__ = None

    @classmethod
    def table_has_rows(cls, session: so.Session) -> bool:
        return session.execute(
            sa.select(sa.literal(True))
            .select_from(cls)
            .limit(1)
        ).scalar() is not None
