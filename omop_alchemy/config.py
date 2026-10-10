from __future__ import annotations

from typing import Annotated, ClassVar

import sqlalchemy as sa
from pydantic import Field
from oa_configurator import (
    CDMDatabaseConfig,
    PackageConfigBase,
    RefTo,
    Resolver,
    ResolvedCDMDatabase,
    SchemaClaim,
    load_stack_config,
)
from orm_loader.backends import staging_schema_claim

MAINTENANCE_SCHEMA: str = "omop_alchemy_maintenance"


class OmopAlchemyConfig(PackageConfigBase):
    """oa-configurator config class for omop-alchemy, the CDM database owner.

    Every downstream package's own ``cdm_db``-named field shares this
    database purely by naming convention (see ``RefTo``), not by importing
    this class.

    Attributes
    ----------
    cdm_db : str
        Name of the ``[databases.*]`` entry holding the CDM database.
    test_cdm_db_pg : str, optional
        Name of the ``[databases.*]`` entry holding the test CDM database,
        marked ``RefTo(CDMDatabaseConfig, is_test=True)``. Must resolve to a
        real PostgreSQL connection; used for real integration testing of
        Postgres-only behavior (FK triggers, catalog queries, ALTER, etc.).
    test_cdm_db_sqlite : str, optional
        Same shape as ``test_cdm_db_pg``, for tests that must always run
        against SQLite specifically (dialect-behavior tests), regardless of
        what ``test_cdm_db_pg`` happens to be configured to. Left
        unconfigured by design in every environment, since
        ``isolated_test_database(..., dialect="sqlite")`` provisions a
        disposable instance with no config needed at all.

    Notes
    -----
    By design, this config is for internal use only and must not be
    imported or resolved by any other package.
    """

    tool_name: ClassVar[str] = "omop_alchemy"
    extra_logging_namespaces: ClassVar[tuple[str, ...]] = ("orm_loader",)

    cdm_db: Annotated[str, RefTo(CDMDatabaseConfig)] = "cdm_db"
    test_cdm_db_pg: Annotated[
        str | None, RefTo(CDMDatabaseConfig, is_test=True)
    ] = Field(
        default=None,
        description="Real PostgreSQL test CDM database, for Postgres-only integration testing.",
    )
    test_cdm_db_sqlite: Annotated[
        str | None, RefTo(CDMDatabaseConfig, is_test=True)
    ] = Field(
        default=None,
        description=(
            "Disposable SQLite test database; left unconfigured by design "
            "(isolated_test_database(..., dialect='sqlite') provisions one automatically)."
        ),
    )

    athena_source_path: str | None = Field(
        default=None,
        description="Path to Athena vocabulary CSV files.",
    )


def get_cdm_context(database: str | None = None) -> tuple[OmopAlchemyConfig, ResolvedCDMDatabase]:
    """Return (pkg_config, resolved_cdm_database), loading config once.

    Parameters
    ----------
    database : str, optional
        Name of a database entry to resolve instead of ``OmopAlchemyConfig.cdm_db``.
        Omit to use the configured default.

    Raises
    ------
    RuntimeError
        If no oa-configurator stack config file exists yet.
    """
    try:
        stack = load_stack_config()
    except FileNotFoundError as exc:
        raise RuntimeError(
            "No omop-alchemy configuration found. "
            "Run `omop-config configure omop_alchemy` to set it up."
        ) from exc
    resolver = Resolver(stack)
    pkg_config = resolver.resolve_package_config(OmopAlchemyConfig)
    resolved = resolver.resolve_database(database or pkg_config.cdm_db)
    if not isinstance(resolved, ResolvedCDMDatabase):
        raise TypeError(
            f"OmopAlchemyConfig.cdm_db must resolve to a CDM database, got "
            f"{type(resolved).__name__}"
        )
    return pkg_config, resolved


def _maintenance_schema_claims() -> list[SchemaClaim]:
    """Schema claims every engine doing maintenance work needs, on both engines."""
    return [
        SchemaClaim(
            schema_tag=MAINTENANCE_SCHEMA,
            physical_schema=MAINTENANCE_SCHEMA,
            reserved=True,
        ),
        staging_schema_claim(),
    ]


def create_cdm_engines(
    resolved: ResolvedCDMDatabase, *, register_claims: bool = True
) -> tuple[sa.Engine, sa.Engine]:
    """Create ``(primary, vocab)`` with the maintenance schemas claimed on both.

    The vocabulary engine is the primary engine itself when the vocabulary
    is not on its own database.

    Parameters
    ----------
    resolved : ResolvedCDMDatabase
    register_claims : bool, optional
        Forwarded to ``create_engines()``. False only checks the claims
        without writing them.
        - For read-only access: False. Does not require CREATE privilege
        - For read/write access: True. Requires CREATE privilege, and will
        raise if the staging schema is already claimed by another package.

    Returns
    -------
    tuple[sqlalchemy.Engine, sqlalchemy.Engine]
    """
    return resolved.create_engines(
        schema_claims=_maintenance_schema_claims(),
        register_claims=register_claims,
    )
