"""Shared utilities: the @omop_command decorator, error handling, and injected CLI parameter definitions."""

from __future__ import annotations

import functools
import inspect
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, TypeVar

import typer
from oa_configurator import ResolvedCDMDatabase
from sqlalchemy.exc import SQLAlchemyError

from .tables import TableCategory
from .ui import console, render_error, render_command_header
from ..backends import BackendNotSupportedError


_F = TypeVar("_F", bound=Callable[..., Any])


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

    # shared across every dry-run/apply-style domain 
    PLANNED = ("planned", Severity.INFO)
    APPLIED = ("applied", Severity.OK)
    SKIPPED = ("skipped", Severity.WARNING)

    # domain-specific "applied" words 
    CREATED = ("created", Severity.OK)
    LOADED = ("loaded", Severity.OK)
    RESET = ("reset", Severity.OK)
    RESTORED = ("restored", Severity.OK)
    CAPTURED = ("captured", Severity.OK)
    READY = ("ready", Severity.OK)
    PASSED = ("passed", Severity.OK)
    MATCHED = ("matched", Severity.OK)

    # warnings: something worth a look, but not blocking
    WARNING = ("warning", Severity.WARNING)
    LIMITED = ("limited", Severity.WARNING)
    DRIFTED = ("drifted", Severity.WARNING)

    # informational: an intentionally supported state
    RENAMED = ("renamed", Severity.INFO)

    # errors/failures
    RELOCATED = ("relocated", Severity.ERROR)
    MISSING = ("missing", Severity.ERROR)
    UNEXPECTED = ("unexpected", Severity.ERROR)
    MISMATCH = ("mismatch", Severity.ERROR)
    BLOCKED = ("blocked", Severity.ERROR)
    UNSUPPORTED = ("unsupported", Severity.ERROR)
    FAILED = ("failed", Severity.ERROR)


@dataclass(frozen=True)
class _ConnContext:
    """Connection context assembled once per CLI command.

    resource_name and athena_source come from OmopAlchemyConfig, not from
    resolved. Everything else a command needs (schema, vocab/results
    schema, test_only) is available via resolved directly, rather than
    duplicated here.
    """
    resolved: ResolvedCDMDatabase
    resource_name: str = ""
    athena_source: str | None = None


# ── Decorator ─────────────────────────────────────────────────────────────────
_NON_WRITING_MODES = frozenset({"dry-run", "inspect"})


def omop_command(
    command_name: str,
    *,
    vocabulary_included: bool | None = None,
    dry_run: bool = False,
    mode_label: str | None = None,
    writes: bool = True,
) -> Callable[[_F], _F]:
    """Decorator that eliminates CLI boilerplate for every omop-alchemy command. Changes the
    typer signature to remove the connection/engine parameters and add a ``--database`` option.

    Resolves the database connection from oa_configurator, calls
    :func:`render_command_header`, and wraps the body in ``try/except handle_error``.

    Notes
    -----
    The decorated function must accept the following positional parameters in order:
    1. ``conn``: a :class:`_ConnContext` object with the resolved database connection
    2. ``engine``: the SQLAlchemy engine for the resolved database

    The third positional parameter, ``vocab_engine``, is optional and only provided if
    the decorated function's signature declares it. The decorator will build and dispose
    a second engine for the vocabulary schema when needed (useful for split CDM
    configurations).

    The decorator also adds a ``--database`` option to the command, allowing users to
    override the default database entry specified in ``OmopAlchemyConfig.cdm_db`` for that
    invocation.

    Parameters
    ----------
    writes : bool, optional
        Whether this command ever writes to the database. False for a
        genuinely read-only command so it can run against a legacy, unbaselined
        database without rasing the adoption-drift error the calling command
        is meant to run *before*. Complements _NON_WRITING_MODES.
    """
    def decorator(func: _F) -> _F:
        orig_params = list(inspect.signature(func).parameters.values())
        wants_vocab = any(p.name == "vocab_engine" for p in orig_params)

        @functools.wraps(func)
        def wrapper(**kwargs: Any) -> Any:
            _dry_run = kwargs.pop("dry_run", False) if dry_run else False
            _database = kwargs.pop("database", None)
            _vocab = kwargs.get("vocabulary_included", vocabulary_included)
            _mode = mode_label if mode_label is not None else ("dry-run" if _dry_run else "apply")
            _register_claims = writes and _mode not in _NON_WRITING_MODES
            try:
                from ..config import create_cdm_engine, get_cdm_context
                pkg_config, resolved = get_cdm_context(_database)
                engine = create_cdm_engine(resolved, register_claims=_register_claims)
                vocab_engine = (
                    resolved.vocab_engine_for(engine, register_claims=_register_claims)
                    if wants_vocab else None
                )
                conn = _ConnContext(
                    resolved=resolved,
                    resource_name=_database or pkg_config.cdm_db,
                    athena_source=pkg_config.athena_source_path,
                )
                console.print(
                    render_command_header(
                        command_name=command_name,
                        engine_url=engine.url.render_as_string(hide_password=True),
                        db_schema=resolved.schema_name,
                        vocabulary_included=_vocab,
                        mode_label=_mode,
                    )
                )
                try:
                    call_kwargs = dict(kwargs)
                    if wants_vocab:
                        call_kwargs["vocab_engine"] = vocab_engine
                    if dry_run:
                        call_kwargs["dry_run"] = _dry_run
                    return func(conn, engine, **call_kwargs)
                finally:
                    engine.dispose()
                    if vocab_engine is not None and vocab_engine is not engine:
                        vocab_engine.dispose()
            except Exception as exc:
                handle_error(exc)

        # Rebuild the Typer-visible signature:
        # • skip conn/engine/vocab_engine (decorator supplies them)
        # • skip dry_run if the decorator owns it
        # • always add --database
        func_params = [
            p for p in orig_params[2:]
            if not (dry_run and p.name == "dry_run")
            and not (wants_vocab and p.name == "vocab_engine")
        ]
        new_params = func_params[:]
        new_params.append(
            inspect.Parameter(
                "database",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=typer.Option(
                    None,
                    "--database",
                    help="Database entry to use instead of the configured default.",
                ),
                annotation=str | None,
            )
        )
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
