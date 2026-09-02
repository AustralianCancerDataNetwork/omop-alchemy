# Materialized views

A materialized view is a persisted read model: an expensive or carefully defined query is computed once and then read like a table. This is useful when the same analytical shape is consumed repeatedly, but it also introduces a deployment lifecycle that ordinary query construction does not have.

OMOP Alchemy owns the OMOP models and the query-building vocabulary. [`orm-loader`](https://australiancancerdatanetwork.github.io/orm-loader/tables/mat_view/) owns the generic materialized-view definition and database lifecycle. Keeping that boundary means this package can describe an OMOP read model without growing a second implementation of DDL, refresh, index creation, or dependency ordering.

## The deployment contract

The supported deployment contract is PostgreSQL with unqualified materialized-view names resolving to the `public` schema through the connection's `search_path`. Leave `schema` at its default when calling the lifecycle methods. Qualified non-`public` schemas and schema translation are not part of this package's materialized-view contract.

This page explains how an OMOP Alchemy query becomes a managed read model. The linked [`orm-loader` materialized-view guide](https://australiancancerdatanetwork.github.io/orm-loader/tables/mat_view/) is the authoritative reference for the complete API, supported options, backend behavior, and generated reference documentation.

The important design decision is the shape of the result. Give the view a stable name, build its contents with the same SQLAlchemy expressions used elsewhere in the toolkit, and make the row grain explicit in the query. The example below creates one row per person and measurement concept, which makes the unique index meaningful as well as useful for concurrent refresh.

```python
import sqlalchemy as sa

from omop_alchemy.cdm.model import Measurement
from orm_loader.mappers.materialised_view_contracts import MaterializedViewIndex
from orm_loader.mappers.materialised_view_mixin import MaterializedViewMixin


measurement_summary = (
    sa.select(
        Measurement.person_id,
        Measurement.measurement_concept_id.label("concept_id"),
        sa.func.max(Measurement.measurement_date).label("last_measurement_date"),
        sa.func.count().label("measurement_count"),
    )
    .group_by(Measurement.person_id, Measurement.measurement_concept_id)
)


class MeasurementSummaryMV(MaterializedViewMixin):
    __mv_name__ = "measurement_summary"
    __mv_select__ = measurement_summary
    __mv_indexes__ = (
        MaterializedViewIndex(
            name="measurement_summary_identity_uq",
            columns=("person_id", "concept_id"),
            unique=True,
        ),
    )
```

`orm-loader` does not infer or validate the logical grain of a selectable. Treat the query's grouping and joins as the source of truth, test that grain against representative data, and declare a matching unique index when the view needs concurrent refresh. The index is the database-level guarantee; a comment or a convention in the Python class is not.

`__mv_dependencies__` is for dependencies between managed materialized views. Its values are matched against the names of the view classes passed to the refresh-order resolver, so a base OMOP table such as `measurement` does not create a lifecycle dependency by itself. Add a dependency when one materialized view reads another:

```python
class PersonMeasurementSummaryMV(MaterializedViewMixin):
    __mv_name__ = "person_measurement_summary"
    __mv_select__ = sa.select(measurement_summary.subquery())
    __mv_dependencies__ = {"measurement_summary"}
```

Use the class as a schema-level definition, not as a promise that OMOP Alchemy will map the resulting relation or schedule its deployment. If application code needs ORM-style reads, map the relation separately according to the application's identity and session requirements.

## Treat creation as deployment

Creation is usually part of an application migration, bootstrap command, or release step. Keep it explicit and make the existing-target policy deliberate:

```python
with engine.begin() as connection:
    MeasurementSummaryMV.create_mv(connection)
```

The default is idempotent: `create_mv()` emits `IF NOT EXISTS`, so an already-present view is left in place. That is convenient for repeatable bootstrap, but it does not detect definition drift. Pass `if_not_exists=False` when an existing target should fail the deployment and force an explicit replacement decision.

The view's declared indexes are created by `create_mv()` by default. Pass `create_indexes=False` only when index creation is intentionally managed elsewhere. With an `Engine`, `orm-loader` manages the transaction for each backend operation; with a `Connection`, the caller controls the transaction and can keep view and index creation inside a larger migration transaction. If index creation fails after the view has been created through an engine, treat the deployment as incomplete and reconcile it before retrying.

`with_data=False` is useful when the relation must exist before its source data is ready, but the resulting view cannot be queried until it has been refreshed:

```python
MeasurementSummaryMV.create_mv(engine, with_data=False, if_not_exists=False)
# Load or migrate the source data, then make the read model available.
MeasurementSummaryMV.refresh_mv(engine)
```

For replacement or teardown, use the lifecycle options documented by [`orm-loader`](https://australiancancerdatanetwork.github.io/orm-loader/tables/mat_view/) rather than issuing raw `CREATE`, `DROP`, or `REFRESH` statements in this package. In particular, `cascade=True` is an explicit decision to remove dependent database objects as part of a drop.

## Choose a refresh policy

An ordinary refresh is the simple operational default:

```python
MeasurementSummaryMV.refresh_mv(engine)
```

Concurrent refresh is a PostgreSQL feature for keeping the existing materialized view available while its contents are rebuilt. It requires an eligible unique index over the view, with no predicate or expression-based shortcut. `orm-loader` first fails closed when the class declares no unique index and then translates PostgreSQL's rejection when the live database still does not satisfy the requirement. Both paths raise `ConcurrentRefreshNotEligibleError`, with the database exception preserved as the cause when PostgreSQL produced one.

```python
from orm_loader.backends import ConcurrentRefreshNotEligibleError


try:
    MeasurementSummaryMV.refresh_mv(engine, concurrently=True)
except ConcurrentRefreshNotEligibleError as error:
    logger.warning("Concurrent refresh unavailable: %s", error)
    MeasurementSummaryMV.refresh_mv(engine)
```

Use the fallback only if serving a briefly stale or synchronously refreshed read model is acceptable. A failed statement can leave a caller-managed PostgreSQL transaction aborted, so roll that transaction back before issuing more statements. An `engine.begin()` context rolls back automatically when an exception leaves the context.

## Orchestrate a family of views

The application owns the registry because it knows which views belong to a deployment and which policy should govern them. `orm-loader` supplies the small amount of generic machinery needed to order managed views and invoke their lifecycle methods.

```python
from orm_loader.mappers.materialised_view_mixin import (
    refresh_all_mvs,
    resolve_mv_refresh_order,
)


ALL_MVS = [
    MeasurementSummaryMV,
    PersonMeasurementSummaryMV,
]

# Ordinary refreshes in dependency order.
refresh_all_mvs(engine, ALL_MVS)

# For concurrent refreshes, retain the same order while choosing the policy.
for view_cls in resolve_mv_refresh_order(ALL_MVS):
    view_cls.refresh_mv(engine, concurrently=True)
```

This registry is also the right place for application-specific choices such as whether to rebuild a view after a definition change, whether a failed refresh should block a release, and how to report lifecycle failures to operators. Do not duplicate the generic dependency resolver or database lifecycle in an OMOP Alchemy toolkit module; the ownership boundary is protected by [`tests/test_materialization_ownership.py`](https://github.com/AustralianCancerDataNetwork/OMOP_Alchemy/blob/main/tests/test_materialization_ownership.py).

For the cohort delivery stack, these deployment concerns belong in `omop-constructs` or the application that runs its migrations and schedulers. OMOP Alchemy's role is to provide reusable OMOP query components and a clear read-model contract.
