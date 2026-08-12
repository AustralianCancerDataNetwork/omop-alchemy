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
)


class OmopAlchemyConfig(PackageConfigBase):
    """oa-configurator config class for omop-alchemy, the CDM database owner.

    Every downstream package's own ``cdm_db``-named field shares this
    database purely by naming convention (see ``RefTo``), not by importing
    this class.

    Attributes
    ----------
    cdm_db : str
        Name of the ``[databases.*]`` entry holding the CDM database.
    test_cdm_db : str, optional
        Name of the ``[databases.*]`` entry holding the test CDM database,
        marked ``RefTo(CDMDatabaseConfig, is_test=True)``.

    Notes
    -----
    By design, this config is for internal use only and must not be
    imported or resolved by any other package.
    """

    tool_name: ClassVar[str] = "omop_alchemy"
    extra_logging_namespaces: ClassVar[tuple[str, ...]] = ("orm_loader",)

    cdm_db: Annotated[str, RefTo(CDMDatabaseConfig)] = "cdm_db"
    test_cdm_db: Annotated[
        str | None, RefTo(CDMDatabaseConfig, is_test=True)
    ] = None

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

    Concept-set expansions are a function of the vocabulary, so caching them
    against this identity means recreating an engine against the same dataset
    reuses the expansion instead of re-running ``concept_ancestor`` traversals.

    Composed from the **vocab** role rather than the primary one, because
    ``concept_ancestor`` is a vocabulary table. On any deployment that does not
    configure a separate vocabulary target this resolves to the CDM database, so
    it costs nothing today and stays correct if vocabulary routing is ever
    honoured by the ORM. Do not "simplify" it to ``resolved.connection``.

    Uses ``safe_url``, the credential-redacted form, so no password reaches a
    cache key.

    **Returns None wherever sharing would be unsafe, so every caller inherits
    that judgement.** Exported precisely so that packages building their own
    engines compose the identity the same way — two spellings of one dataset
    would produce two cache entries that each look authoritative. That only works
    if the safety conditions live here rather than at one call site.

    Two conditions yield None:

    *Split vocabulary target.* Vocabulary models use the primary logical schema,
    and one SQLAlchemy engine cannot route tables to a second physical
    connection, so a declared vocabulary target that differs from the primary is
    not what the engine actually reads. Returning its identity would let two
    different primary databases that name the same external vocabulary share
    expansions — one database's concept sets served for another. Such a
    deployment falls back to per-engine caching until ORM routing supports it.

    *Ephemeral database.* In-memory SQLite, where two engines built from
    identical configuration are genuinely separate databases.

    Both cases are correct-but-unshared rather than wrong.
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
