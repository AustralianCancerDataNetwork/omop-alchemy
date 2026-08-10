"""Shared utilities: the @omop_command decorator, error handling, and injected CLI parameter definitions."""

from __future__ import annotations

import functools
import inspect
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, TypeVar

import sqlalchemy as sa
import typer
from orm_loader.backends import STAGING_SCHEMA
from sqlalchemy.exc import SQLAlchemyError

from .tables import TableCategory
from .ui import console, render_error, render_command_header
from ..backends import BackendNotSupportedError, resolve_backend


_F = TypeVar("_F", bound=Callable[..., Any])


class ReservedSchema(StrEnum):
    """Schema names reserved for OMOP_Alchemy/orm-loader internal bookkeeping.
    A user-configured db_schema may never collide with one of these.
    """

    STAGING = STAGING_SCHEMA
    MAINTENANCE = "omop_alchemy_maintenance"


def reject_reserved_schema(db_schema: str | None) -> None:
    """Raise if db_schema collides with a schema name reserved for internal bookkeeping."""
    if db_schema in set(ReservedSchema):
        raise RuntimeError(
            f"db_schema cannot be {db_schema!r}: reserved for OMOP_Alchemy/orm-loader internal use."
        )


class Severity(StrEnum):
    """Coarse-grained outcome classification shared by every maintenance
    command's status vocabulary.

    Parameters
    ----------
    code : str
        The severity's string value.
    style : str
        Rich color/style name used to render any Status with this severity.
    """

    def __new__(cls, code: str, style: str):
        obj = str.__new__(cls, code)
        obj._value_ = code
        return obj

    def __init__(self, code: str, style: str):
        self.style = style

    OK = ("ok", "green")
    INFO = ("info", "cyan")
    WARNING = ("warning", "yellow")
    ERROR = ("error", "red")


class Status(StrEnum):
    """A maintenance command result status, carrying its Severity (and, via
    Severity.style, its render color).

    Parameters
    ----------
    code : str
        The status's string value (unchanged from the plain strings used
        before this type existed, so existing string comparisons/dict
        lookups by value keep working).
    severity : Severity
        How severe this status is, and (via severity.style) how it renders.
    """

    def __new__(cls, code: str, severity: Severity):
        obj = str.__new__(cls, code)
        obj._value_ = code
        return obj

    def __init__(self, code: str, severity: Severity):
        self.severity = severity

    # -- shared across every dry-run/apply-style domain --
    PLANNED = ("planned", Severity.INFO)
    APPLIED = ("applied", Severity.OK)
    SKIPPED = ("skipped", Severity.WARNING)

    # -- domain-specific "applied" words (backup, index restore/capture,
    #    sequence reset, vocab load) -- same OK severity as APPLIED, kept as
    #    distinct words since the specific outcome is worth seeing at a glance --
    CREATED = ("created", Severity.OK)
    LOADED = ("loaded", Severity.OK)
    RESET = ("reset", Severity.OK)
    RESTORED = ("restored", Severity.OK)
    CAPTURED = ("captured", Severity.OK)
    READY = ("ready", Severity.OK)
    PASSED = ("passed", Severity.OK)
    MATCHED = ("matched", Severity.OK)

    # -- warnings: something worth a look, but not blocking --
    WARNING = ("warning", Severity.WARNING)
    LIMITED = ("limited", Severity.WARNING)
    DRIFTED = ("drifted", Severity.WARNING)

    # -- informational: an intentionally supported state --
    RENAMED = ("renamed", Severity.INFO)

    # -- errors/failures --
    MISSING = ("missing", Severity.ERROR)
    UNEXPECTED = ("unexpected", Severity.ERROR)
    MISMATCH = ("mismatch", Severity.ERROR)
    BLOCKED = ("blocked", Severity.ERROR)
    UNSUPPORTED = ("unsupported", Severity.ERROR)
    FAILED = ("failed", Severity.ERROR)


@dataclass(frozen=True)
class _ConnContext:
    """Connection context derived from the oa_configurator resolved resource."""
    db_schema: str | None
    engine_url: str = ""
    athena_source: str | None = None  # from OmopAlchemyConfig.athena_source_path


# ── Decorator ─────────────────────────────────────────────────────────────────

def omop_command(
    command_name: str,
    *,
    vocabulary_included: bool | None = None,
    dry_run: bool = False,
    mode_label: str | None = None,
) -> Callable[[_F], _F]:
    """Decorator that eliminates CLI boilerplate for every omop-alchemy command.

    Resolves the database connection from oa_configurator, calls
    :func:`render_command_header`, and wraps the body in ``try/except handle_error``.

    The decorated function must accept ``(conn, engine, ...)`` as its first two
    positional parameters. The decorator supplies them.
    """
    def decorator(func: _F) -> _F:
        @functools.wraps(func)
        def wrapper(**kwargs: Any) -> Any:
            _dry_run = kwargs.pop("dry_run", False) if dry_run else False
            _vocab = kwargs.get("vocabulary_included", vocabulary_included)
            _mode = mode_label if mode_label is not None else ("dry-run" if _dry_run else "apply")
            try:
                from ..config import create_cdm_engine, get_cdm_context
                pkg_config, resolved = get_cdm_context()
                reject_reserved_schema(resolved.schema_name)
                engine = create_cdm_engine(resolved)
                conn = _ConnContext(
                    db_schema=resolved.schema_name,
                    engine_url=engine.url.render_as_string(hide_password=True),
                    athena_source=pkg_config.athena_source_path,
                )
                console.print(
                    render_command_header(
                        command_name=command_name,
                        engine_url=conn.engine_url,
                        db_schema=conn.db_schema,
                        vocabulary_included=_vocab,
                        mode_label=_mode,
                    )
                )
                try:
                    if dry_run:
                        return func(conn, engine, dry_run=_dry_run, **kwargs)
                    return func(conn, engine, **kwargs)
                finally:
                    engine.dispose()
            except Exception as exc:
                handle_error(exc)

        # Rebuild the Typer-visible signature:
        # • skip conn/engine (decorator supplies them)
        # • skip dry_run if the decorator owns it
        orig_params = list(inspect.signature(func).parameters.values())
        func_params = [
            p for p in orig_params[2:]
            if not (dry_run and p.name == "dry_run")
        ]
        new_params = func_params[:]
        if dry_run:
            new_params.append(
                inspect.Parameter(
                    "dry_run",
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    default=typer.Option(
                        False,
                        "--dry-run",
                        help="Preview planned actions without applying any changes to the database.",
                    ),
                    annotation=bool,
                )
            )
        wrapper.__signature__ = inspect.signature(func).replace(parameters=new_params)  # ty: ignore[unresolved-attribute]

        return wrapper  # ty: ignore[invalid-return-type]
    return decorator


# ── Helpers ───────────────────────────────────────────────────────────────────

def ensure_schema(engine: sa.Engine, schema: str | None) -> None:
    """Create the schema in the database if it does not already exist.

    Delegates to the active backend so behaviour is correct for each dialect.
    No-op when schema is None or ``"public"``, or on backends that don't
    support named schemas (e.g. SQLite).
    """
    if not schema or schema == "public":
        return
    backend = resolve_backend(engine)
    with engine.connect() as conn:
        backend.ensure_schema(conn, schema)
        conn.commit()


def handle_error(exc: Exception) -> None:
    if isinstance(exc, BackendNotSupportedError):
        console.print(render_error(f"Not supported: {exc}"))
        raise typer.Exit(code=1) from exc
    if isinstance(exc, RuntimeError):
        console.print(render_error(str(exc)))
        raise typer.Exit(code=1) from exc
    if isinstance(exc, SQLAlchemyError):
        detail = str(exc).strip()
        message = f"Database operation failed: {exc.__class__.__name__}."
        if detail:
            message = f"{message} Detail: {detail}"
        console.print(render_error(message))
        raise typer.Exit(code=1) from exc
    raise exc


def dry_status(dry_run: bool, applied: Status = Status.APPLIED) -> Status:
    """Return Status.PLANNED when dry_run is True, otherwise the applied status."""
    return Status.PLANNED if dry_run else applied


def dry_label(dry_run: bool, planned: str, applied: str) -> str:
    """Return the planned or applied detail string based on dry_run."""
    return planned if dry_run else applied


def resolve_selection(
    *,
    scope: TableCategory | None,
    tables: list[str] | None,
    default_scope: TableCategory | None = None,
) -> tuple[TableCategory | None, tuple[str, ...] | None]:
    if scope is not None and tables:
        raise RuntimeError("Use either `--scope` or `--table`, not both.")
    selected = tuple(tables) if tables else None
    if selected is not None:
        return None, selected
    return scope or default_scope, None
