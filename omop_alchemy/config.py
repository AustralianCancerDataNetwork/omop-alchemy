from __future__ import annotations

from typing import ClassVar

import sqlalchemy as sa
from pydantic import Field
from oa_configurator import (
    DatabaseConfig,
    PackageConfigBase,
    ResourceSpec,
    Resolver,
    ResolvedResource,
    load_stack_config,
)

from omop_alchemy.toolkit.core.concepts import register_vocabulary_identity


# Mapping of PostgreSQL SQLAlchemy drivernames to the Python module they require.
# Kept here (not in oa_configurator) because the driver choice and install instructions
# are OMOP_Alchemy-specific — orm-loader ≥ 0.4.0 dropped the implicit psycopg2 dependency.
_POSTGRES_DRIVER_MODULES: dict[str, str] = {
    "postgresql": "psycopg",
    "postgresql+psycopg": "psycopg",
    "postgresql+psycopg2": "psycopg2",
}


def _missing_driver_message(url: str, exc: ModuleNotFoundError) -> str | None:
    """Return an install hint if exc is a missing PostgreSQL driver, else None."""
    drivername = sa.engine.make_url(url).drivername
    expected = _POSTGRES_DRIVER_MODULES.get(drivername)
    if expected is None:
        return None
    missing = exc.name
    if missing is None and expected in str(exc):
        missing = expected
    if missing != expected:
        return None
    return (
        f"Database driver '{expected}' is required for dialect '{drivername}' "
        "but is not installed. "
        "Install PostgreSQL support with "
        "`uv sync --extra postgres` or `pip install -e '.[postgres]'`."
    )


class OmopAlchemyConfig(PackageConfigBase):
    CDM_DB: ClassVar[ResourceSpec] = ResourceSpec(
        semantic_name="cdm_db",
        display_name="OMOP CDM Database",
        description="Database containing the OMOP CDM tables and vocabulary.",
        connection_name_hint="cdm",
    )
    TEST_DB: ClassVar[ResourceSpec] = ResourceSpec(
        semantic_name="test_cdm_db",
        display_name="Test OMOP CDM Database",
        description=(
            "Dedicated PostgreSQL database for running integration tests. "
            "Tests drop and recreate the entire public schema on every run."
        ),
        connection_name_hint="pg_test",
        cdm_schema_default="public",
        connection_defaults=DatabaseConfig(
            dialect="postgresql+psycopg",
            host="localhost",
            port=55432,
            user="test",
            password="test",
            database_name="test_db",
        ),
    )

    tool_name: ClassVar[str] = "omop_alchemy"
    extra_logging_namespaces: ClassVar[tuple[str, ...]] = ("orm_loader",)
    required_resources: ClassVar[tuple[str, ...]] = (CDM_DB.semantic_name,)
    owned_resources: ClassVar[tuple[ResourceSpec, ...]] = (CDM_DB,)
    test_resources: ClassVar[tuple[ResourceSpec, ...]] = (TEST_DB,)

    athena_source_path: str | None = Field(
        default=None,
        description="Path to Athena vocabulary CSV files.",
    )


def get_cdm_context() -> tuple[OmopAlchemyConfig, ResolvedResource]:
    """Return (pkg_config, resolved_cdm_resource), loading config once.

    The resource is taken from tools.omop_alchemy.default_resource when set;
    otherwise falls back to the canonical CDM_DB resource name.

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
    pkg_config = OmopAlchemyConfig.from_stack(stack)
    tool = stack.tools.get(OmopAlchemyConfig.tool_name)
    resource_name = (tool.default_resource if tool else None) or OmopAlchemyConfig.CDM_DB.semantic_name
    resolved = Resolver(stack).resolve_resource(resource_name)
    return pkg_config, resolved


def vocabulary_identity(resolved: ResolvedResource) -> str | None:
    """Stable identity for the vocabulary dataset ``resolved`` reads, or None.

    Concept-set expansions are a function of the vocabulary, so caching them
    against this identity means recreating an engine against the same dataset
    reuses the expansion instead of re-running ``concept_ancestor`` traversals.

    Composed from the **vocab** role rather than the primary one, because
    ``concept_ancestor`` is a vocabulary table. On any deployment that does not
    configure a separate vocabulary target this resolves to the CDM database, so
    it costs nothing today and stays correct if vocabulary routing is ever
    honoured by the ORM. Do not "simplify" it to ``resolved.database``.

    Uses ``safe_url``, the credential-redacted form, so no password reaches a
    cache key.

    Returns None for an ephemeral database — in-memory SQLite, where two engines
    built from identical configuration are genuinely separate databases. Those
    fall back to per-engine caching, which is correct but not shared. Exported so
    that packages building their own engines compose the identity the same way;
    two spellings of one dataset would produce two cache entries that each look
    authoritative.
    """
    target = getattr(resolved, "vocab_database", None)
    safe_url = getattr(target, "safe_url", None)
    schema = getattr(resolved, "vocab_schema", None)

    # Anything we cannot read as a concrete string leaves identity undetermined,
    # which means per-engine caching: correct, just not shared. Failing safe here
    # matters because a wrong identity would share one dataset's concept sets with
    # another, whereas a missing one only costs a rebuild.
    if not isinstance(safe_url, str) or not isinstance(schema, str):
        return None
    if _is_ephemeral_url(safe_url):
        return None
    return f"{safe_url}|{schema}"


def _is_ephemeral_url(safe_url: str) -> bool:
    """Whether ``safe_url`` names a database that cannot be shared across engines."""
    lowered = safe_url.lower()
    if not lowered.startswith("sqlite"):
        return False
    _, _, target = lowered.partition("://")
    target = target.lstrip("/")
    return target in ("", ":memory:") or "mode=memory" in lowered


def create_cdm_engine(resolved: ResolvedResource) -> sa.Engine:
    """Create the CDM SQLAlchemy engine with helpful PostgreSQL driver error messages."""
    try:
        engine = resolved.create_engine()
    except ModuleNotFoundError as exc:
        msg = _missing_driver_message(resolved.database.url, exc)
        if msg is not None:
            raise RuntimeError(msg) from exc
        raise

    # Register against the engine we return: create_engine may hand back a derived
    # OptionEngine, and that is the object sessions bind to.
    identity = vocabulary_identity(resolved)
    if identity is not None:
        register_vocabulary_identity(engine, identity)
    return engine
