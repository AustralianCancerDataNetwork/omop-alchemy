"""Vocabulary loading command for Athena CDM CSV files."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias, cast

import sqlalchemy as sa
import sqlalchemy.orm as so
import sqlalchemy.event as sae
from sqlalchemy.exc import OperationalError
import typer
from sqlalchemy.pool import NullPool
from orm_loader.backends import resolve_backend
from orm_loader.tables.typing import CSVTableProtocol
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

from ..backends.resolve import SupportedDialect
from omop_alchemy.cdm.model.vocabulary import (
    Concept,
    Concept_Ancestor,
    Concept_Class,
    Concept_Relationship,
    Concept_Synonym,
    Domain,
    Drug_Strength,
    Relationship,
    Source_To_Concept_Map,
    Vocabulary,
)

from ._cli_utils import (
    ReservedSchema,
    Status,
    ensure_schema,
    omop_command,
    reject_reserved_schema,
)
from .cli_foreign_keys import manage_foreign_key_triggers
from .cli_indexes import manage_indexes
from .cli_tables import reset_model_sequences
from .tables import TableCategory, schema_adjusted_metadata, select_maintenance_tables
from .ui import (
    console,
    render_error,
    render_vocab_index_warnings,
    render_vocab_load_results,
    render_vocab_load_summary,
)

MergeStrategy: TypeAlias = Literal["replace", "upsert", "insert_if_empty"]
QuoteMode: TypeAlias = Literal["by_delimiter", "auto", "csv", "literal"]

VocabularyModel: TypeAlias = type[CSVTableProtocol]
VocabularyLoadProgressCallback: TypeAlias = Callable[["VocabularyLoadProgress"], None]


@dataclass(frozen=True)
class VocabularyLoadResult:
    """Outcome of loading one Athena vocabulary CSV file via the staged ORM CSV loader."""

    table_name: str
    status: Status
    row_count: int | None
    csv_path: str | None
    required: bool
    detail: str


@dataclass(frozen=True)
class VocabularyLoadReport:
    """Complete vocabulary load report: per-table outcomes and load session metadata."""

    source_path: str
    backend: str
    db_schema: str | None
    merge_strategy: MergeStrategy
    created_table_count: int
    sequence_reset_count: int
    results: tuple[VocabularyLoadResult, ...]
    index_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class VocabularyLoadProgress:
    """Progress event emitted during vocabulary load. Drives the CLI progress bar."""

    description: str
    percent: float
    table_done: bool = False
    table_name: str | None = None
    rows_this_table: int | None = None
    table_count: int = 0


def _emit(
    callback: VocabularyLoadProgressCallback | None,
    description: str,
    percent: float,
    **kwargs,
) -> None:
    if callback is not None:
        callback(
            VocabularyLoadProgress(description=description, percent=percent, **kwargs)
        )


REQUIRED_VOCAB_MODELS: tuple[VocabularyModel, ...] = cast(
    tuple[VocabularyModel, ...],
    (
        Domain,
        Vocabulary,
        Concept_Class,
        Relationship,
        Concept,
        Concept_Ancestor,
        Concept_Relationship,
        Concept_Synonym,
    ),
)

OPTIONAL_VOCAB_MODELS: tuple[VocabularyModel, ...] = cast(
    tuple[VocabularyModel, ...],
    (
        Drug_Strength,
        Source_To_Concept_Map,
    ),
)

_ALL_VOCAB_TABLE_NAMES: frozenset[str] = frozenset(
    m.__tablename__ for m in REQUIRED_VOCAB_MODELS + OPTIONAL_VOCAB_MODELS
)


class VocabularyLoadError(RuntimeError):
    """Raised when a single Athena vocabulary table load fails."""


_RETRYABLE_FRAGMENTS: tuple[str, ...] = (
    "recovery mode",
    "connection reset",
    "server closed the connection",
)


def _is_retryable_error(exc: Exception) -> bool:
    """True for transient PostgreSQL connection failures that are worth retrying."""
    msg = str(exc).lower()
    return isinstance(exc, OperationalError) and any(
        s in msg for s in _RETRYABLE_FRAGMENTS
    )


def _is_missing_staging_table_error(
    exc: Exception,
    *,
    model: VocabularyModel,
    session: so.Session,
) -> bool:
    """Return True if the exception is a ProgrammingError caused by the staging table not existing yet."""
    staging_table_name = resolve_backend(session).staging_name_for_table(
        model.__tablename__
    )
    message = str(exc).lower()
    return (
        exc.__class__.__name__ == "ProgrammingError"
        and "does not exist" in message
        and staging_table_name.lower() in message
    )


def _load_vocab_model_csv(
    session: so.Session,
    *,
    model: VocabularyModel,
    csv_path: Path,
    merge_strategy: MergeStrategy,
    quote_mode: QuoteMode = "by_delimiter",
    chunksize: int | None = None,
    index_strategy: str = "auto",
    merge_batch_size: int | None = None,
    staging_schema: str | None = None,
) -> int:
    """Call model.load_csv. If the staging table is absent, create it and retry once."""
    load_kwargs: dict[str, object] = {
        "merge_strategy": merge_strategy,
        "quote_mode": quote_mode,
        "index_strategy": index_strategy,
        "merge_batch_size": merge_batch_size,
        "staging_schema": staging_schema,
    }
    if chunksize is not None:
        load_kwargs["chunksize"] = chunksize

    try:
        return int(model.load_csv(session, csv_path, **load_kwargs))  # ty: ignore[invalid-argument-type]
    except Exception as exc:
        if not _is_missing_staging_table_error(exc, model=model, session=session):
            raise

        session.rollback()
        model.create_staging_table(session)
        return int(model.load_csv(session, csv_path, **load_kwargs))  # ty: ignore[invalid-argument-type]


def _find_vocab_csv_path(source_path: Path, table_name: str) -> Path | None:
    """Locate the CSV file for table_name under source_path, trying exact name, lower, upper, and case-insensitive glob."""
    direct_candidates = (
        source_path / f"{table_name}.csv",
        source_path / f"{table_name.lower()}.csv",
        source_path / f"{table_name.upper()}.csv",
    )
    for candidate in direct_candidates:
        if candidate.exists():
            return candidate

    for candidate in sorted(source_path.glob("*.csv")):
        if candidate.stem.lower() == table_name:
            return candidate

    return None


def _missing_required_files(
    source_path: Path,
    required_models: tuple[VocabularyModel, ...] = REQUIRED_VOCAB_MODELS,
) -> list[str]:
    """Return the table names of vocabulary CSVs in required_models that cannot be found under source_path."""
    return [
        model.__tablename__
        for model in required_models
        if _find_vocab_csv_path(source_path, model.__tablename__) is None
    ]


def _create_missing_vocabulary_tables(
    connection: sa.Connection,
    *,
    db_schema: str | None,
) -> int:
    """Create any vocabulary-category ORM tables that are absent from the target database. Returns the count created."""
    vocab_tables = select_maintenance_tables(
        categories=(TableCategory.VOCABULARY,),
    )
    inspector = sa.inspect(connection)
    missing_tables = [
        table
        for table in vocab_tables
        if not inspector.has_table(table.table_name, schema=db_schema)
    ]
    if not missing_tables:
        return 0

    metadata, adjusted_tables = schema_adjusted_metadata(
        vocab_tables,
        db_schema=db_schema,
    )
    metadata.create_all(
        bind=connection,
        tables=[adjusted_tables[table.table_name] for table in missing_tables],
        checkfirst=True,
    )
    return len(missing_tables)


def load_vocab_source(
    engine: sa.Engine,
    *,
    source_path: str | Path,
    tables: list[str] | None = None,
    db_schema: str | None = None,
    dry_run: bool = False,
    merge_strategy: MergeStrategy = "replace",
    quote_mode: QuoteMode = "by_delimiter",
    chunksize: int | None = 100_000,
    bulk_mode: bool = True,
    merge_batch_size: int | None = None,
    progress_callback: VocabularyLoadProgressCallback | None = None,
) -> VocabularyLoadReport:
    """
    Load Athena vocabulary CSVs from source_path into the target database.

    When tables is None (default), all vocabulary tables are loaded and the
    full set of required CSVs is validated before any DB work begins. When
    tables is provided, only those tables are loaded and the preflight check
    is scoped to the requested tables only; an absent CSV for any named table
    raises RuntimeError before touching the database.

    With bulk_mode (default on PostgreSQL), secondary indexes and FK triggers
    are toggled globally around the load for speed. Pass --no-bulk-mode when
    loading a single table to avoid the index drop/rebuild overhead.
    """
    resolved_source_path = Path(source_path).expanduser().resolve()
    if not resolved_source_path.exists() or not resolved_source_path.is_dir():
        raise RuntimeError(f"Athena source directory not found: {resolved_source_path}")

    if tables is not None:
        unknown = sorted(set(tables) - _ALL_VOCAB_TABLE_NAMES)
        if unknown:
            raise RuntimeError(
                f"Unknown vocabulary table(s): {unknown}. "
                f"Valid names: {sorted(_ALL_VOCAB_TABLE_NAMES)}"
            )

    all_models = REQUIRED_VOCAB_MODELS + OPTIONAL_VOCAB_MODELS
    if tables is not None:
        all_models = tuple(m for m in all_models if m.__tablename__ in tables)

    # When a specific table selection is given, check exactly those CSVs are present.
    # Otherwise check only the required tables, missing optional CSVs are allowed.
    models_to_preflight = all_models if tables is not None else REQUIRED_VOCAB_MODELS
    missing = _missing_required_files(
        resolved_source_path, required_models=models_to_preflight
    )
    if missing:
        if tables is not None:
            raise RuntimeError(
                "Requested vocabulary CSV file(s) not found in source directory: "
                + ", ".join(sorted(missing))
            )
        raise RuntimeError(
            "Missing required Athena vocabulary CSV files: "
            + ", ".join(sorted(missing))
        )

    reject_reserved_schema(db_schema)

    if not dry_run:
        ensure_schema(engine, db_schema)
        ensure_schema(engine, ReservedSchema.STAGING)

    # NullPool: each session/connection is opened fresh and closed immediately after
    # use. No stale pooled connections survive between tables, which prevents
    # "connection in recovery mode" failures on subsequent tables after a heavy load.
    load_engine = sa.create_engine(engine.url, poolclass=NullPool)
    if db_schema is not None:
        # NullPool discards the DBAPI connection on every commit, so a one-time
        # SET search_path on the first checkout doesn't survive into the next
        # checkout (e.g. after create_staging_table's commit). Re-apply on every
        # new connection via an engine-level connect event so COPY and raw-cursor
        # operations always target the right schema. The staging schema no longer
        # needs to be on the path because orm-loader now qualifies staging
        # table identifiers explicitly.
        _quoted_schema = '"' + db_schema.replace('"', '""') + '"'

        @sae.listens_for(load_engine, "connect")
        def _set_search_path(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute(f"SET search_path TO {_quoted_schema}")
            cur.close()

    table_count = sum(
        1
        for m in all_models
        if _find_vocab_csv_path(resolved_source_path, m.__tablename__) is not None
    )

    results: list[VocabularyLoadResult] = []
    created_table_count = 0
    sequence_reset_count = 0
    rows_cumulative = 0
    table_index = 0
    index_warnings: tuple[str, ...] = ()

    _emit(
        progress_callback,
        f"Preparing Athena vocabulary load for {table_count} CSV file(s)",
        0.0,
        table_count=table_count,
    )

    _use_bulk_mode = (
        bulk_mode and not dry_run and engine.dialect.name == SupportedDialect.POSTGRESQL
    )
    if _use_bulk_mode:
        _emit(
            progress_callback,
            "Disabling FK trigger checks for bulk load...",
            0.0,
            table_count=table_count,
        )
        manage_foreign_key_triggers(
            engine,
            enable=False,
            vocabulary_included=True,
            db_schema=db_schema,
            dry_run=False,
        )
        _emit(
            progress_callback,
            "Dropping indexes for bulk load...",
            0.0,
            table_count=table_count,
        )
        disable_results = manage_indexes(
            engine,
            enable=False,
            vocabulary_included=True,
            db_schema=db_schema,
            dry_run=False,
        )
        index_warnings = tuple(
            f"{result.table_name}.{result.index_name}: {result.detail}"
            for result in disable_results
            if result.status == Status.WARNING
        )
        if index_warnings:
            _emit(
                progress_callback,
                f"{len(index_warnings)} index(es) could not be optimized for bulk load (left in place)",
                0.0,
                table_count=table_count,
            )

    if not dry_run:
        with load_engine.connect() as pre_conn:
            created_table_count = _create_missing_vocabulary_tables(
                pre_conn, db_schema=db_schema
            )
            pre_conn.commit()

    try:
        for model in all_models:
            csv_path = _find_vocab_csv_path(resolved_source_path, model.__tablename__)
            required = model in REQUIRED_VOCAB_MODELS

            if csv_path is None:
                results.append(
                    VocabularyLoadResult(
                        table_name=model.__tablename__,
                        status=Status.SKIPPED,
                        row_count=None,
                        csv_path=None,
                        required=required,
                        detail="optional Athena CSV not found; table skipped",
                    )
                )
                continue

            table_index += 1

            _emit(
                progress_callback,
                f"Loading {model.__tablename__} ({table_index}/{table_count})...",
                (table_index - 1) / table_count * 100,
                table_name=model.__tablename__,
                table_count=table_count,
            )

            if dry_run:
                results.append(
                    VocabularyLoadResult(
                        table_name=model.__tablename__,
                        status=Status.PLANNED,
                        row_count=None,
                        csv_path=str(csv_path),
                        required=required,
                        detail=f"Athena CSV would be loaded via staged ORM CSV loader (quote_mode={quote_mode!r})",
                    )
                )
                continue

            recovery_hint = (
                " Indexes and FK triggers may still be disabled; run "
                "'omop-alchemy indexes enable --vocab' and 'omop-alchemy foreign-keys enable' to recover."
                if _use_bulk_mode
                else ""
            )

            row_count = 0
            _prev_attempt_was_crash = False
            for attempt in range(3):
                try:
                    with so.Session(load_engine) as session:
                        if (
                            _prev_attempt_was_crash
                            and merge_strategy == "insert_if_empty"
                        ):
                            # A DB crash left partial data committed in this table.
                            # Truncate so insert_if_empty can retry cleanly. Safe because
                            # bulk_mode's manage_foreign_key_triggers ran ALTER TABLE ...
                            # DISABLE TRIGGER ALL on all vocabulary tables, and that state
                            # persists across crash+recovery in pg_trigger.tgenabled.
                            # Schema-qualified explicitly so this targets the CDM table
                            # regardless of search_path ordering.
                            table_ref = (
                                f'"{db_schema}"."{model.__tablename__}"'
                                if db_schema
                                else f'"{model.__tablename__}"'
                            )
                            session.execute(sa.text(f"TRUNCATE TABLE {table_ref}"))
                            session.commit()
                        row_count = _load_vocab_model_csv(
                            session,
                            model=model,
                            csv_path=csv_path,
                            merge_strategy=merge_strategy,
                            quote_mode=quote_mode,
                            index_strategy="keep" if _use_bulk_mode else "auto",
                            chunksize=chunksize,
                            merge_batch_size=merge_batch_size,
                            staging_schema=ReservedSchema.STAGING,
                        )
                        session.commit()
                    break
                except Exception as exc:
                    if attempt < 2 and _is_retryable_error(exc):
                        _prev_attempt_was_crash = True
                        time.sleep(10)
                        continue
                    raise VocabularyLoadError(
                        "Athena vocabulary load failed for "
                        f"table `{model.__tablename__}` from `{csv_path}` "
                        f"using merge strategy `{merge_strategy}` on backend `{engine.dialect.name}`. "
                        f"Underlying error: {exc.__class__.__name__}: {exc}"
                        + recovery_hint
                    ) from exc

            rows_cumulative += row_count
            results.append(
                VocabularyLoadResult(
                    table_name=model.__tablename__,
                    status=Status.LOADED,
                    row_count=row_count,
                    csv_path=str(csv_path),
                    required=required,
                    detail=f"Athena CSV loaded via staged ORM CSV loader (quote_mode={quote_mode!r})",
                )
            )

            _emit(
                progress_callback,
                f"Loaded {model.__tablename__} ({table_index}/{table_count})",
                table_index / table_count * 100,
                table_done=True,
                table_name=model.__tablename__,
                rows_this_table=row_count,
                table_count=table_count,
            )
    finally:
        if _use_bulk_mode:
            _emit(
                progress_callback,
                "Rebuilding indexes on vocabulary tables (may take 15+ min)...",
                100.0,
                table_count=table_count,
            )
            manage_indexes(
                engine,
                enable=True,
                vocabulary_included=True,
                db_schema=db_schema,
                dry_run=False,
                cluster=False,
            )
            _emit(
                progress_callback,
                "Re-enabling FK trigger checks...",
                100.0,
                table_count=table_count,
            )
            manage_foreign_key_triggers(
                engine,
                enable=True,
                vocabulary_included=True,
                db_schema=db_schema,
                dry_run=False,
            )

    _emit(
        progress_callback,
        "Athena vocabulary load complete",
        100.0,
        table_count=table_count,
    )

    if not dry_run and engine.dialect.name == SupportedDialect.POSTGRESQL:
        sequence_results = reset_model_sequences(
            engine,
            db_schema=db_schema,
            vocabulary_included=True,
            dry_run=False,
        )
        sequence_reset_count = sum(
            result.status == Status.RESET for result in sequence_results
        )

    return VocabularyLoadReport(
        source_path=str(resolved_source_path),
        backend=engine.dialect.name,
        db_schema=db_schema,
        merge_strategy=merge_strategy,
        created_table_count=created_table_count,
        sequence_reset_count=sequence_reset_count,
        results=tuple(results),
        index_warnings=index_warnings,
    )


app = typer.Typer(rich_markup_mode="rich")


@app.command(
    "load-vocab-source",
    help="Load Athena vocabulary CSV files from a configured source path using the ORM staged CSV loader.",
)
@omop_command("load-vocab-source", vocabulary_included=True, dry_run=True)
def load_vocab_source_command(
    conn,
    engine,
    athena_source: str | None = typer.Option(
        None,
        help="Path to the unzipped Athena vocabulary CSV directory. Falls back to the saved athena-source default.",
    ),
    tables: list[str] | None = typer.Option(
        None,
        "--table",
        help=(
            "Restrict the load to one or more vocabulary tables by name "
            "(e.g. --table concept --table vocabulary). "
            "When omitted, all vocabulary tables are loaded. "
            "Skips the all-required-files preflight check; "
            "errors if the named CSV is absent from the source directory."
        ),
    ),
    merge_strategy: MergeStrategy = typer.Option(
        "replace",
        help=(
            "CSV merge strategy. `replace` (default) keeps the DB in sync with the source. "
            "`upsert` is incremental and non-destructive. "
            "`insert_if_empty` is the fast path for a fresh empty target."
        ),
    ),
    quote_mode: QuoteMode = typer.Option(
        "by_delimiter",
        help=(
            "How the vocabulary CSV loader interprets double-quotes. `by_delimiter` (default) "
            "uses literal quotes for tab-delimited Athena files and RFC-4180 quoting for "
            "comma-delimited files. `auto` samples file content; `csv` and `literal` force "
            "one mode for every file."
        ),
    ),
    staging_chunk_size: int | None = typer.Option(
        100_000,
        help=(
            "[Phase 1] Rows per ORM transaction when loading CSV → staging table. "
            "Ignored when the PostgreSQL COPY fast-path is active (the default for "
            "Athena CSVs). Pass 0 to disable chunking entirely."
        ),
    ),
    bulk_mode: bool = typer.Option(
        True,
        "--bulk-mode/--no-bulk-mode",
        help=(
            "Disable FK triggers and drop indexes globally before loading, then rebuild after. "
            "Much faster than per-table management for a full vocabulary reload. "
            "Requires FK trigger and index management support; ignored on backends that do not support it. "
            "If the load fails mid-way, run `indexes enable --vocab` and `foreign-keys enable` to recover."
        ),
    ),
    merge_batch_size: int | None = typer.Option(
        None,
        help=(
            "[Phase 2] Rows per transaction when merging staging → target table. "
            "Default: None (no pagination — single INSERT per table, fastest for high-RAM systems). "
            "Set to a positive integer to enable paginated commits for memory-constrained systems; "
            "note that pagination adds a COUNT query and an index build on the staging table "
            "before the merge begins, which adds significant overhead even at large batch sizes."
        ),
    ),
    dry_run: bool = False,
) -> None:
    """Load all Athena vocabulary CSVs from the configured source path, optionally toggling indexes and FK triggers for speed."""
    effective_athena_source = athena_source or conn.athena_source
    if effective_athena_source is None:
        console.print(
            render_error(
                "No Athena vocabulary source path is configured. "
                "Set it with `omop-config configure omop_alchemy` "
                "or pass `--athena-source`."
            )
        )
        raise typer.Exit(code=1)

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=None),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        task_id = progress.add_task(
            "Preparing Athena vocabulary load...", total=100.0, completed=0
        )
        completed_tables: list[str] = []

        def _update_progress(event: VocabularyLoadProgress) -> None:
            progress.update(
                task_id, completed=event.percent, description=event.description
            )
            if event.table_done and event.table_name is not None:
                completed_tables.append(event.table_name)
                row_info = (
                    f": [dim]{event.rows_this_table:,} rows[/dim]"
                    if event.rows_this_table is not None
                    else ""
                )
                progress.console.print(
                    f"[green]loaded[/green] [bold]{event.table_name}[/bold]{row_info} "
                    f"({len(completed_tables)}/{event.table_count})"
                )

        report = load_vocab_source(
            engine,
            source_path=effective_athena_source,
            tables=tables or None,
            db_schema=conn.db_schema,
            dry_run=dry_run,
            merge_strategy=merge_strategy,
            quote_mode=quote_mode,
            chunksize=None if staging_chunk_size == 0 else staging_chunk_size,
            bulk_mode=bulk_mode,
            merge_batch_size=merge_batch_size,
            progress_callback=_update_progress,
        )
        progress.update(
            task_id, completed=100.0, description="Athena vocabulary load complete"
        )

    console.print(render_vocab_load_results(report.results))
    console.print(render_vocab_load_summary(report, dry_run=dry_run))
    index_warnings_panel = render_vocab_index_warnings(report)
    if index_warnings_panel is not None:
        console.print(index_warnings_panel)
