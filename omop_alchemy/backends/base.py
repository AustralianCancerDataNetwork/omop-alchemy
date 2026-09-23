from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
import sqlalchemy as sa
from oa_configurator import Role

if TYPE_CHECKING:
    from sqlalchemy.sql import ColumnElement


# ── Fulltext types ────────────────────────────────────────────────────────────

CONCEPT_NAME_TSVECTOR_COLUMN = "concept_name_tsvector"
CONCEPT_SYNONYM_NAME_TSVECTOR_COLUMN = "concept_synonym_name_tsvector"


@dataclass(frozen=True)
class FullTextTargetConfig:
    """Primitive fulltext target owned by the backend — no ORM dependencies."""
    table_name: str
    source_column_name: str
    vector_column_name: str
    index_name: str


class FullTextError(RuntimeError):
    """Raised when a full-text search maintenance operation fails."""


# ── Backend errors ────────────────────────────────────────────────────────────

class BackendNotSupportedError(RuntimeError):
    """Raised when no backend exists for a dialect, or a backend does not implement a required operation."""


class FeatureNotSupportedError(BackendNotSupportedError):
    """Raised when a backend exists but doesn't implement a specific feature."""

    def __init__(self, feature: str, backend: "Backend") -> None:
        super().__init__(f"'{feature}' is not supported by the {backend.name} backend.")
        self.feature = feature
        self.backend_name = backend.name


def backend_supports(backend: "Backend", method_name: str) -> bool:
    """True if this backend class overrides *method_name* from the base Backend class."""
    return getattr(type(backend), method_name) is not getattr(Backend, method_name)


def require_backend_support(backend: "Backend", method_name: str, feature: str) -> None:
    """Raise FeatureNotSupportedError if this backend does not override *method_name*."""
    if not backend_supports(backend, method_name):
        raise FeatureNotSupportedError(feature, backend)


def backend_support_note(method_name: str) -> str:
    """Return a help-text note listing which known backends support *method_name*.

    Derived purely from the class hierarchy — no manual list to maintain.
    """
    from .resolve import _DIALECT_TO_BACKEND_MAP

    supported = sorted(
        b.name for b in _DIALECT_TO_BACKEND_MAP.values()
        if backend_supports(b, method_name)
    )
    return f"Supported backends: {', '.join(supported)}." if supported else "Not supported by any backend."


class Backend(ABC):

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def dialect(self) -> str: ...

    # ── FK trigger management ────────────────────────────────────────────────

    def toggle_fk_triggers(
        self,
        conn: sa.Connection,
        table_name: str,
        *,
        enable: bool,
        schema_tag: str = Role.PRIMARY.value,
    ) -> None:
        raise FeatureNotSupportedError("FK trigger management", self)

    def get_fk_trigger_counts(
        self,
        conn: sa.Connection,
        table_name: str,
        *,
        schema_tag: str = Role.PRIMARY.value,
    ) -> tuple[int, int]:
        """Return (disabled_count, enabled_count) for RI triggers on the table."""
        raise FeatureNotSupportedError("FK trigger status inspection", self)

    def count_fk_violations(
        self,
        conn: sa.Connection,
        source_table: str,
        referred_table: str,
        constrained_cols: list[str],
        referred_cols: list[str],
        *,
        source_schema_tag: str = Role.PRIMARY.value,
        referred_schema_tag: str = Role.PRIMARY.value,
    ) -> int:
        raise FeatureNotSupportedError("FK constraint violation counting", self)

    # ── Clustering ───────────────────────────────────────────────────────────

    def cluster_table(
        self,
        conn: sa.Connection,
        table_name: str,
        index_name: str,
        *,
        schema_tag: str = Role.PRIMARY.value,
    ) -> None:
        raise FeatureNotSupportedError("Table clustering", self)

    def get_clustered_index_name(
        self,
        conn: sa.Connection,
        table_name: str,
        *,
        schema_tag: str = Role.PRIMARY.value,
    ) -> str | None:
        raise FeatureNotSupportedError("Cluster index inspection", self)

    # ── Schema reconciliation ────────────────────────────────────────────────

    def normalize_index_expression(self, sql_text: str) -> str:
        """Canonicalize a reflected functional-index expression for comparison
        against the ORM's own compiled expression text.

        A backend's own catalog reflection can introduce dialect-specific
        canonicalization noise (implicit casts, identifier case) that the
        ORM's compiled text never has. Only called when a backend actually
        reflects expression-based indexes back with real expression text to
        normalize; a backend that can't (e.g. SQLite never reflects them at
        all) never reaches this call.
        """
        raise FeatureNotSupportedError("Functional-index expression normalization", self)

    # ── Row counts ───────────────────────────────────────────────────────────

    def approximate_row_counts(
        self,
        conn: sa.Connection,
        schema: str,
    ) -> dict[str, int]:
        """Cheap, catalog-based row-count estimate per table in schema.

        Unlike most Backend methods, schema is explicit rather than read via
        physical_schema_of(conn): a caller previewing an orphan schema is inspecting a
        schema other than the connection's own configured one.
        """
        raise FeatureNotSupportedError("Approximate row counts", self)

    # ── Table operations ─────────────────────────────────────────────────────

    @abstractmethod
    def analyze_table(
        self,
        conn: sa.Connection,
        table_name: str,
        *,
        vacuum: bool = False,
        schema_tag: str = Role.PRIMARY.value,
    ) -> None: ...

    def index_exists(
        self,
        conn: sa.Connection,
        index_name: str,
        *,
        schema_tag: str = Role.PRIMARY.value,
    ) -> bool:
        """Return True when the named index currently exists on the database.

        Backends should implement this with native catalog queries rather than
        SQLAlchemy reflection so expression-based indexes are handled correctly.
        """
        raise FeatureNotSupportedError("Index existence check", self)

    def drop_index_if_exists(
        self,
        conn: sa.Connection,
        index_name: str,
        *,
        schema_tag: str = Role.PRIMARY.value,
    ) -> None:
        """Drop an index by name without relying on SQLAlchemy's reflection-based checkfirst.

        Some backends (e.g. SQLite) can't reflect expression-based indexes, so
        Index.drop(checkfirst=True) would silently no-op on them. IF EXISTS is
        evaluated by the database itself, not by reflection. schema_tag is
        accepted for interface parity with the schema-aware override
        (PostgresBackend); this default implementation is unqualified,
        matching SQLite having no schema concept to route through.
        """
        conn.exec_driver_sql(f'DROP INDEX IF EXISTS "{index_name}"')

    def truncate_table_batch(
        self,
        conn: sa.Connection,
        table_names: list[str],
        *,
        restart_identities: bool,
        cascade: bool,
        schema_tag: str = Role.PRIMARY.value,
    ) -> None:
        raise FeatureNotSupportedError("TRUNCATE with RESTART IDENTITY / CASCADE", self)

    # ── Sequence management ──────────────────────────────────────────────────

    def find_sequence_name(
        self,
        conn: sa.Connection,
        table_name: str,
        column_name: str,
        *,
        schema_tag: str = Role.PRIMARY.value,
    ) -> str | None:
        raise FeatureNotSupportedError("Owned sequence lookup", self)

    def set_sequence_value(
        self,
        conn: sa.Connection,
        sequence_name: str,
        value: int,
    ) -> None:
        raise FeatureNotSupportedError("Sequence value reset", self)

    # ── Full-text search ─────────────────────────────────────────────────────

    @property
    def fulltext_targets(self) -> tuple[FullTextTargetConfig, ...]:
        """Return the fulltext target configs managed by this backend. Empty by default."""
        return ()

    def register_fulltext_metadata(self) -> None:
        """Append tsvector sidecar columns to SQLAlchemy ORM metadata for this backend's targets."""
        raise FeatureNotSupportedError("Full-text metadata registration", self)

    def unregister_fulltext_metadata(self) -> None:
        """Remove tsvector sidecar columns from SQLAlchemy ORM metadata."""
        raise FeatureNotSupportedError("Full-text metadata unregistration", self)

    def concept_name_tsvector_expression(
        self, *, regconfig: str = "english"
    ) -> "ColumnElement[Any]":
        """Return a SQLAlchemy expression for the concept_name tsvector."""
        raise FeatureNotSupportedError("Full-text search expression", self)

    def concept_synonym_name_tsvector_expression(
        self, *, regconfig: str = "english"
    ) -> "ColumnElement[Any]":
        """Return a SQLAlchemy expression for the concept_synonym_name tsvector."""
        raise FeatureNotSupportedError("Full-text search expression", self)

    def install_fulltext_on_table(
        self,
        conn: sa.Connection,
        *,
        table_name: str,
        vector_column_name: str,
        index_name: str,
        create_indexes: bool,
        fastupdate: bool,
        schema_tag: str = Role.PRIMARY.value,
    ) -> None:
        raise FeatureNotSupportedError("Full-text search", self)

    def populate_fulltext_on_table(
        self,
        conn: sa.Connection,
        *,
        table_name: str,
        vector_column_name: str,
        source_column_name: str,
        regconfig: str,
        schema_tag: str = Role.PRIMARY.value,
    ) -> int | None:
        raise FeatureNotSupportedError("Full-text search", self)

    def drop_fulltext_on_table(
        self,
        conn: sa.Connection,
        *,
        table_name: str,
        vector_column_name: str,
        index_name: str,
        drop_indexes: bool,
        schema_tag: str = Role.PRIMARY.value,
    ) -> None:
        raise FeatureNotSupportedError("Full-text search", self)

    # ── Backup / restore ─────────────────────────────────────────────────────

    def prepare_backup(
        self,
        engine: sa.Engine,
        output_path: str,
        backup_format: str,
        *,
        schema_tag: str = Role.PRIMARY.value,
    ) -> tuple[str, list[str], dict[str, str], str]:
        """Return (tool_path, command, env, database_name). subprocess.run stays in CLI."""
        raise FeatureNotSupportedError("Database backup", self)

    def prepare_restore(
        self,
        engine: sa.Engine,
        input_path: str,
        backup_format: str,
        *,
        schema_tag: str = Role.PRIMARY.value,
    ) -> tuple[str, list[str], dict[str, str], str]:
        """Return (tool_path, command, env, database_name). subprocess.run stays in CLI."""
        raise FeatureNotSupportedError("Database restore", self)

