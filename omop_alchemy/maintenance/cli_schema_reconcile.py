"""Schema reconciliation domain: comparing ORM metadata against the live database column types, indexes, FK constraints, and cluster state."""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from oa_configurator import (
    ResolvedDatabase,
    Role,
    find_table_in_other_schemas,
    role_of_table,
    supports_schemas
)
from sqlalchemy.engine.interfaces import ReflectedForeignKeyConstraint, ReflectedIndex

from ..backends import Backend, backend_supports, resolve_backend
from ._cli_utils import Severity, Status
from .cli_indexes import _cluster_column_names, _cluster_target_name, _find_equivalent_index
from .tables import (
    TableCategory,
    select_maintenance_tables,
)


def is_blocking_issue(issue: ReconciliationIssue) -> bool:
    """Whether a reconciliation issue represents actual drift requiring attention.

    Parameters
    ----------
    issue : ReconciliationIssue
        A single reconciliation issue.

    Returns
    -------
    bool
        True when the issue's status has Severity.ERROR.
    """
    return issue.status.severity == Severity.ERROR


@dataclass(frozen=True)
class ReconciliationIssue:
    """A single schema drift detail: column, index, FK, or cluster mismatch between ORM metadata and the database."""

    table_name: str
    category: TableCategory
    component: str
    object_name: str
    status: Status
    expected: str | None
    actual: str | None
    detail: str


@dataclass(frozen=True)
class TableReconciliationResult:
    """Per-table schema reconciliation summary: whether ORM metadata matches the live database."""

    table_name: str
    category: TableCategory
    status: Status
    issue_count: int
    detail: str


@dataclass(frozen=True)
class SchemaReconciliationReport:
    """Complete reconciliation report across all selected ORM-managed tables."""

    backend: str
    table_results: tuple[TableReconciliationResult, ...]
    issues: tuple[ReconciliationIssue, ...]


def _effective_schema(
    resolved: ResolvedDatabase | None, role: Role, db_schema: str | None
) -> str | None:
    """resolved.schema_for_role(role) when given, else db_schema regardless
    of role, the fallback for a caller with no resolved object to hand.
    """
    return resolved.schema_for_role(role) if resolved is not None else db_schema


def _schema_qualified_tables(
    resolved: ResolvedDatabase | None, db_schema: str | None
) -> dict[int, sa.Table]:
    """Schema-qualified copy of every table in Base.metadata, keyed by id() of the original.

    Each table is qualified to its own role's schema via resolved, not one
    blanket value, since a vocab-role table can live in a different physical
    schema than a clinical one. Copied together into one MetaData() (not per
    table): to_metadata() never brings a referenced table along on its own,
    and an FK's target needs its copy already present in the same metadata.
    Returns the tables unchanged, keyed by their own id, when resolved and
    db_schema are both None.
    """
    from orm_loader.helpers import Base

    if resolved is None and db_schema is None:
        return {id(table): table for table in Base.metadata.tables.values()}
    metadata = sa.MetaData()

    def _referred_schema(_table: sa.Table, _to_schema, _constraint, referred_schema: str | None):
        # None means "unchanged" to to_metadata(); BLANK_SCHEMA is what actually clears a schema tag.
        target = _effective_schema(resolved, Role(referred_schema), db_schema)
        return target if target is not None else sa.BLANK_SCHEMA

    return {
        id(table): table.to_metadata(
            metadata,
            # SQLAlchemy's own stub omits None from schema's declared type,
            # despite accepting and correctly handling it at runtime
            schema=_effective_schema(resolved, role_of_table(table), db_schema),  # ty: ignore[invalid-argument-type]
            referred_schema_fn=_referred_schema,
        )
        for table in Base.metadata.tables.values()
    }


def _normalized_type(type_: sa.types.TypeEngine[object], dialect: sa.engine.Dialect) -> str:
    """Compile a SQLAlchemy type to its dialect-specific string and normalise whitespace/case for comparison."""
    return type_.compile(dialect=dialect).lower().replace(" ", "")


def _expected_foreign_keys(
    table: sa.Table,
) -> dict[tuple[tuple[str, ...], str, tuple[str, ...]], sa.ForeignKeyConstraint]:
    """Index ORM-defined FK constraints by (constrained_cols, referred_table, referred_cols) for diffing."""
    expected: dict[tuple[tuple[str, ...], str, tuple[str, ...]], sa.ForeignKeyConstraint] = {}
    for constraint in table.foreign_key_constraints:
        constrained_columns = tuple(element.parent.name for element in constraint.elements)
        referred_columns = tuple(element.column.name for element in constraint.elements)
        referred_table = constraint.referred_table.name
        expected[(constrained_columns, referred_table, referred_columns)] = constraint
    return expected


def _actual_foreign_keys(
    inspector: sa.Inspector,
    table_name: str,
    db_schema: str | None,
) -> dict[tuple[tuple[str, ...], str, tuple[str, ...]], ReflectedForeignKeyConstraint]:
    """Index live FK constraints from the database inspector by the same key tuple used by _expected_foreign_keys."""
    actual: dict[tuple[tuple[str, ...], str, tuple[str, ...]], ReflectedForeignKeyConstraint] = {}
    for foreign_key in inspector.get_foreign_keys(table_name, schema=db_schema):
        constrained_columns = tuple(foreign_key.get("constrained_columns") or [])
        referred_columns = tuple(foreign_key.get("referred_columns") or [])
        referred_table = str(foreign_key.get("referred_table"))
        actual[(constrained_columns, referred_table, referred_columns)] = foreign_key
    return actual


def _expected_indexes(table: sa.Table) -> dict[str, sa.Index]:
    """Return ORM-defined named indexes for a table, keyed by index name."""
    return {
        str(index.name): index
        for index in table.indexes
        if index.name is not None
    }


def _actual_indexes(
    inspector: sa.Inspector,
    table_name: str,
    db_schema: str | None,
) -> dict[str, ReflectedIndex]:
    """Return live named indexes from the database inspector, keyed by index name."""
    return {
        str(index["name"]): index
        for index in inspector.get_indexes(table_name, schema=db_schema)
        if index.get("name") is not None
    }


def _expected_index_signature(index: sa.Index, backend: Backend) -> tuple[str, ...]:
    """Per-position signature for index: a plain column's name, or the
    normalized compiled SQL text of an expression (e.g. ``func.lower(...)``),
    matching how the database reflects a functional index back. Expression
    normalization is dialect-specific (e.g. Postgres's own catalog inserts
    casts as reflection noise), so it's delegated to backend.
    """
    signature = []
    for expr in index.expressions:
        if isinstance(expr, sa.Column):
            signature.append(expr.name)
        elif isinstance(expr, str):
            signature.append(backend.normalize_index_expression(expr))
        else:
            compiled = str(expr.compile(compile_kwargs={"literal_binds": True}))
            signature.append(backend.normalize_index_expression(compiled))
    return tuple(signature)


def _actual_index_signature(actual_index: ReflectedIndex, backend: Backend) -> tuple[str, ...]:
    """Per-position signature for a reflected index, matching
    :func:`_expected_index_signature`'s shape.
    """
    column_names = actual_index.get("column_names") or []
    if "expressions" not in actual_index:
        return tuple(name for name in column_names if name is not None)
    expressions = iter(actual_index.get("expressions") or [])
    return tuple(
        name if name is not None else backend.normalize_index_expression(next(expressions))
        for name in column_names
    )


def reconcile_schema(
    engine: sa.Engine,
    *,
    resolved: ResolvedDatabase | None = None,
    db_schema: str | None = None,
    vocabulary_included: bool = False,
) -> SchemaReconciliationReport:
    """Compare ORM metadata against the live database schema.

    Parameters
    ----------
    engine : sqlalchemy.Engine
        Engine to inspect. Its dialect selects the backend used for
        cluster-state checks.
    resolved : ResolvedDatabase, optional
        When given, qualifies each table to its own role's schema
        (schema_name/vocab_schema/results_schema) instead of applying
        db_schema to every table regardless of role.
    db_schema : str, optional
        Blanket schema applied to every table when resolved is not given.
    vocabulary_included : bool, optional
        Whether vocabulary tables are included in the diff.

    Returns
    -------
    SchemaReconciliationReport
        Per-table status plus every column, index, FK, and cluster issue
        found.
    """
    excluded_categories: tuple[TableCategory, ...] = (
        () if vocabulary_included else (TableCategory.VOCABULARY,)
    )
    _backend = resolve_backend(engine)
    _cross_schema_fk_supported = supports_schemas(engine)
    selected_tables = select_maintenance_tables(exclude_categories=excluded_categories)
    schema_qualified_tables = _schema_qualified_tables(resolved, db_schema)
    inspector = sa.inspect(engine)
    all_issues: list[ReconciliationIssue] = []
    table_results: list[TableReconciliationResult] = []

    with engine.connect() as connection:
        for maintenance_table in selected_tables:
            table_issues: list[ReconciliationIssue] = []
            table_role = role_of_table(maintenance_table.table)
            table_schema = _effective_schema(resolved, table_role, db_schema)
            exists = inspector.has_table(maintenance_table.table_name, schema=table_schema)
            if not exists:
                relocated_to = find_table_in_other_schemas(
                    engine, maintenance_table.table_name, expected_schema=table_schema
                )
                if relocated_to:
                    detail = (
                        f"Table is absent from schema {table_schema!r} but found in "
                        f"{', '.join(sorted(relocated_to))!r}."
                    )
                    table_issues.append(
                        ReconciliationIssue(
                            table_name=maintenance_table.table_name,
                            category=maintenance_table.category,
                            component="table",
                            object_name=maintenance_table.table_name,
                            status=Status.RELOCATED,
                            expected=table_schema,
                            actual=", ".join(sorted(relocated_to)),
                            detail=detail,
                        )
                    )
                    table_results.append(
                        TableReconciliationResult(
                            table_name=maintenance_table.table_name,
                            category=maintenance_table.category,
                            status=Status.RELOCATED,
                            issue_count=1,
                            detail=detail,
                        )
                    )
                    all_issues.extend(table_issues)
                    continue
                table_issues.append(
                    ReconciliationIssue(
                        table_name=maintenance_table.table_name,
                        category=maintenance_table.category,
                        component="table",
                        object_name=maintenance_table.table_name,
                        status=Status.MISSING,
                        expected="present",
                        actual="absent",
                        detail="ORM-managed table is missing from the target database.",
                    )
                )
                table_results.append(
                    TableReconciliationResult(
                        table_name=maintenance_table.table_name,
                        category=maintenance_table.category,
                        status=Status.MISSING,
                        issue_count=1,
                        detail="Table is missing from the target database.",
                    )
                )
                all_issues.extend(table_issues)
                continue

            expected_table = schema_qualified_tables[id(maintenance_table.table)]
            expected_columns = {column.name: column for column in expected_table.columns}
            actual_columns = {
                str(column["name"]): column
                for column in inspector.get_columns(maintenance_table.table_name, schema=table_schema)
            }
            actual_pk_names = tuple(
                inspector.get_pk_constraint(maintenance_table.table_name, schema=table_schema).get("constrained_columns") or []
            )
            expected_pk_names = tuple(column.name for column in expected_table.primary_key.columns)

            for column_name, column in expected_columns.items():
                if column_name not in actual_columns:
                    table_issues.append(
                        ReconciliationIssue(
                            table_name=maintenance_table.table_name,
                            category=maintenance_table.category,
                            component="column",
                            object_name=column_name,
                            status=Status.MISSING,
                            expected=_normalized_type(column.type, engine.dialect),
                            actual=None,
                            detail="Column is defined in ORM metadata but missing from the database.",
                        )
                    )

            for column_name, column in actual_columns.items():
                if column_name not in expected_columns:
                    table_issues.append(
                        ReconciliationIssue(
                            table_name=maintenance_table.table_name,
                            category=maintenance_table.category,
                            component="column",
                            object_name=column_name,
                            status=Status.UNEXPECTED,
                            expected=None,
                            actual=_normalized_type(column["type"], engine.dialect),
                            detail="Column exists in the database but is not defined in ORM metadata.",
                        )
                    )

            for column_name in sorted(set(expected_columns).intersection(actual_columns)):
                expected_column = expected_columns[column_name]
                actual_column = actual_columns[column_name]
                expected_type = _normalized_type(expected_column.type, engine.dialect)
                actual_type = _normalized_type(actual_column["type"], engine.dialect)
                if expected_type != actual_type:
                    table_issues.append(
                        ReconciliationIssue(
                            table_name=maintenance_table.table_name,
                            category=maintenance_table.category,
                            component="column",
                            object_name=column_name,
                            status=Status.MISMATCH,
                            expected=expected_type,
                            actual=actual_type,
                            detail="Column type differs from ORM metadata.",
                        )
                    )

                expected_nullable = False if column_name in expected_pk_names else bool(expected_column.nullable)
                actual_nullable = False if column_name in actual_pk_names else bool(actual_column["nullable"])
                if expected_nullable != actual_nullable:
                    table_issues.append(
                        ReconciliationIssue(
                            table_name=maintenance_table.table_name,
                            category=maintenance_table.category,
                            component="column",
                            object_name=column_name,
                            status=Status.MISMATCH,
                            expected="nullable" if expected_nullable else "not nullable",
                            actual="nullable" if actual_nullable else "not nullable",
                            detail="Column nullability differs from ORM metadata.",
                        )
                    )

            if expected_pk_names != actual_pk_names:
                table_issues.append(
                    ReconciliationIssue(
                        table_name=maintenance_table.table_name,
                        category=maintenance_table.category,
                        component="primary_key",
                        object_name=maintenance_table.table_name,
                        status=Status.MISMATCH,
                        expected=", ".join(expected_pk_names),
                        actual=", ".join(actual_pk_names) if actual_pk_names else None,
                        detail="Primary key columns differ from ORM metadata.",
                    )
                )

            expected_fks = _expected_foreign_keys(expected_table)
            actual_fks = _actual_foreign_keys(inspector, maintenance_table.table_name, table_schema)
            # Uses the unqualified table, since two role-tagged tables can collapse to
            # the same schema (e.g. all-None on SQLite) and hide a genuine cross-role FK.
            raw_expected_fks = _expected_foreign_keys(maintenance_table.table)

            for signature, constraint in expected_fks.items():
                if signature not in actual_fks:
                    raw_constraint = raw_expected_fks.get(signature)
                    if (
                        not _cross_schema_fk_supported
                        and raw_constraint is not None
                        and role_of_table(raw_constraint.referred_table) != table_role
                    ):
                        # SQLite can never create an inline FK crossing a schema boundary.
                        continue
                    constrained_columns, referred_table, referred_columns = signature
                    table_issues.append(
                        ReconciliationIssue(
                            table_name=maintenance_table.table_name,
                            category=maintenance_table.category,
                            component="foreign_key",
                            object_name=constraint.name if isinstance(constraint.name, str) else ",".join(constrained_columns),
                            status=Status.MISSING,
                            expected=f"{','.join(constrained_columns)} -> {referred_table}({','.join(referred_columns)})",
                            actual=None,
                            detail="Foreign key is defined in ORM metadata but missing from the database.",
                        )
                    )

            for signature, foreign_key in actual_fks.items():
                if signature not in expected_fks:
                    constrained_columns, referred_table, referred_columns = signature
                    table_issues.append(
                        ReconciliationIssue(
                            table_name=maintenance_table.table_name,
                            category=maintenance_table.category,
                            component="foreign_key",
                            object_name=str(foreign_key.get("name") or ",".join(constrained_columns)),
                            status=Status.UNEXPECTED,
                            expected=None,
                            actual=f"{','.join(constrained_columns)} -> {referred_table}({','.join(referred_columns)})",
                            detail="Foreign key exists in the database but is not defined in ORM metadata.",
                        )
                    )

            expected_idxs = _expected_indexes(expected_table)
            actual_idxs = _actual_indexes(inspector, maintenance_table.table_name, table_schema)
            actual_index_list = list(actual_idxs.values())
            renamed_actual_names: set[str] = set()

            for index_name, index in expected_idxs.items():
                if index_name not in actual_idxs:
                    expected_columns_for_index = tuple(column.name for column in index.columns)
                    equivalent_name = _find_equivalent_index(
                        actual_index_list, expected_columns_for_index, bool(index.unique)
                    )
                    if equivalent_name is not None:
                        renamed_actual_names.add(equivalent_name)
                        table_issues.append(
                            ReconciliationIssue(
                                table_name=maintenance_table.table_name,
                                category=maintenance_table.category,
                                component="index",
                                object_name=index_name,
                                status=Status.RENAMED,
                                expected=index_name,
                                actual=equivalent_name,
                                detail=(
                                    f"Index is present under a different name ('{equivalent_name}') "
                                    f"than ORM metadata expects ('{index_name}')."
                                ),
                            )
                        )
                    else:
                        table_issues.append(
                            ReconciliationIssue(
                                table_name=maintenance_table.table_name,
                                category=maintenance_table.category,
                                component="index",
                                object_name=index_name,
                                status=Status.MISSING,
                                expected=", ".join(column.name for column in index.columns),
                                actual=None,
                                detail="Index is defined in ORM metadata but missing from the database.",
                            )
                        )
                    continue

                actual_index = actual_idxs[index_name]
                expected_signature = _expected_index_signature(index, _backend)
                actual_signature = _actual_index_signature(actual_index, _backend)
                if expected_signature != actual_signature:
                    table_issues.append(
                        ReconciliationIssue(
                            table_name=maintenance_table.table_name,
                            category=maintenance_table.category,
                            component="index",
                            object_name=index_name,
                            status=Status.MISMATCH,
                            expected=", ".join(expected_signature),
                            actual=", ".join(actual_signature) if actual_signature else None,
                            detail="Index columns differ from ORM metadata.",
                        )
                    )
                if bool(index.unique) != bool(actual_index.get("unique")):
                    table_issues.append(
                        ReconciliationIssue(
                            table_name=maintenance_table.table_name,
                            category=maintenance_table.category,
                            component="index",
                            object_name=index_name,
                            status=Status.MISMATCH,
                            expected="unique" if index.unique else "non-unique",
                            actual="unique" if actual_index.get("unique") else "non-unique",
                            detail="Index uniqueness differs from ORM metadata.",
                        )
                    )

            if backend_supports(_backend, "get_clustered_index_name"):
                expected_cluster = _cluster_target_name(maintenance_table)
                actual_cluster = _backend.get_clustered_index_name(
                    connection,
                    maintenance_table.table_name,
                    role=table_role,
                )
                if expected_cluster != actual_cluster:
                    # May be a rename, not drift, so treat like a renamed index.
                    # Ignore uniqueness (irrelevant to CLUSTER). Runs before
                    # the unexpected-index loop below and registers the match
                    # in renamed_actual_names so that loop doesn't double-flag it.
                    renamed_cluster = False
                    equivalent_cluster_name: str | None = None
                    if expected_cluster is not None and actual_cluster is not None:
                        expected_cluster_columns = _cluster_column_names(
                            maintenance_table, expected_cluster
                        )
                        equivalent_cluster_name = _find_equivalent_index(
                            actual_index_list,
                            expected_cluster_columns,
                            None,
                        )
                        renamed_cluster = equivalent_cluster_name == actual_cluster
                    if renamed_cluster:
                        assert equivalent_cluster_name is not None
                        renamed_actual_names.add(equivalent_cluster_name)
                        table_issues.append(
                            ReconciliationIssue(
                                table_name=maintenance_table.table_name,
                                category=maintenance_table.category,
                                component="cluster",
                                object_name=maintenance_table.table_name,
                                status=Status.RENAMED,
                                expected=expected_cluster,
                                actual=actual_cluster,
                                detail="Table is clustered on a differently-named equivalent index.",
                            )
                        )
                    else:
                        table_issues.append(
                            ReconciliationIssue(
                                table_name=maintenance_table.table_name,
                                category=maintenance_table.category,
                                component="cluster",
                                object_name=maintenance_table.table_name,
                                status=(
                                    Status.MISSING
                                    if expected_cluster and not actual_cluster
                                    else Status.UNEXPECTED
                                    if actual_cluster and not expected_cluster
                                    else Status.MISMATCH
                                ),
                                expected=expected_cluster,
                                actual=actual_cluster,
                                detail="Table clustering differs from ORM metadata.",
                            )
                        )

            for index_name, index in actual_idxs.items():
                if index_name not in expected_idxs and index_name not in renamed_actual_names:
                    table_issues.append(
                        ReconciliationIssue(
                            table_name=maintenance_table.table_name,
                            category=maintenance_table.category,
                            component="index",
                            object_name=index_name,
                            status=Status.UNEXPECTED,
                            expected=None,
                            actual=", ".join(c for c in (index.get("column_names") or []) if c is not None),
                            detail="Index exists in the database but is not defined in ORM metadata.",
                        )
                    )

            blocking_issues = [issue for issue in table_issues if is_blocking_issue(issue)]
            table_status = Status.MATCHED if not blocking_issues else Status.DRIFTED
            table_results.append(
                TableReconciliationResult(
                    table_name=maintenance_table.table_name,
                    category=maintenance_table.category,
                    status=table_status,
                    issue_count=len(table_issues),
                    detail=(
                        "No differences detected."
                        if not table_issues
                        else f"{len(table_issues)} difference(s) detected."
                    ),
                )
            )
            all_issues.extend(table_issues)

    return SchemaReconciliationReport(
        backend=engine.dialect.name,
        table_results=tuple(table_results),
        issues=tuple(all_issues),
    )
