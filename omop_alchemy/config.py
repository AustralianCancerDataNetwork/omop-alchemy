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
    Role,
    load_stack_config,
    register_reserved_schema,
)

# Guaranteed to be imported and registered if there is a config
MAINTENANCE_SCHEMA: str = "omop_alchemy_maintenance"

register_reserved_schema(MAINTENANCE_SCHEMA, owner="omop_alchemy")


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


def get_cdm_context() -> tuple[OmopAlchemyConfig, ResolvedCDMDatabase]:
    """Return (pkg_config, resolved_cdm_database), loading config once.

    The CDM database is always whatever ``OmopAlchemyConfig.cdm_db`` resolves
    to -- point a deployment at a second CDM instance via that field's own
    ``--cdm-db`` flag at configure time, not a call-site override.

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
    resolved = resolver.resolve_database(pkg_config.cdm_db)
    if not isinstance(resolved, ResolvedCDMDatabase):
        raise TypeError(
            f"OmopAlchemyConfig.cdm_db must resolve to a CDM database, got "
            f"{type(resolved).__name__}"
        )
    return pkg_config, resolved


def vocabulary_identity(resolved: ResolvedCDMDatabase) -> str | None:
    """Stable identity for the vocabulary dataset ``resolved`` reads, or None.

    Caches concept-set expansions (``concept_ancestor`` traversals) across
    engines reading the same vocabulary. Built from the VOCAB role, not
    primary, since ``concept_ancestor`` is a vocabulary table; do not
    simplify to ``resolved.connection``. Uses ``safe_url`` so no password
    reaches the cache key.

    Returns None wherever sharing would be unsafe, so every caller inherits
    that judgement instead of each composing its own identity: 
    - a split vocabulary target: schema_translate_map cannot route to a different
    physical connection, so identity based on the declared target would not
    match what the engine actually reads, or 
    - an ephemeral database: in-memory SQLite, where identically-configured engines are genuinely
    separate databases. 
    
    Both fall back to per-engine caching instead of being wrong.
    """
    vocab_target = resolved.connection_target(Role.VOCAB)

    if (
        vocab_target.safe_url != resolved.connection.safe_url
        or resolved.vocab_schema != resolved.schema_name
    ):
        return None

    if _is_ephemeral_url(vocab_target.safe_url):
        return None

    return f"{vocab_target.safe_url}|{resolved.vocab_schema}"


def _is_ephemeral_url(safe_url: str) -> bool:
    """Whether ``safe_url`` names a database that cannot be shared across engines."""
    lowered = safe_url.lower()
    if not lowered.startswith("sqlite"):
        return False
    _, _, target = lowered.partition("://")
    target = target.lstrip("/")
    return target in ("", ":memory:") or "mode=memory" in lowered


def create_cdm_engine(resolved: ResolvedCDMDatabase) -> sa.Engine:
    """Create the CDM engine and register its vocabulary cache identity."""
    engine = resolved.create_engine()

    # Imported here rather than at module scope: toolkit.core.concepts reaches
    # cdm.model, and `import omop_alchemy` runs this module, so a module-level
    # import would pull the entire CDM model tree into package init.
    from omop_alchemy.toolkit.core.concepts import register_vocabulary_identity

    # Register against the engine we return: create_engine may hand back a derived
    # OptionEngine, and that is the object sessions bind to. vocabulary_identity
    # returns None wherever sharing would be unsafe, so there is no extra
    # condition to apply here -- and no condition for other registrars to forget.
    identity = vocabulary_identity(resolved)
    if identity is not None:
        register_vocabulary_identity(engine, identity)
    return engine
