# Materialized views

Materialized-view definitions and database lifecycle operations are provided by
[`orm-loader`](https://australiancancerdatanetwork.github.io/orm-loader/tables/mat_view/).
OMOP Alchemy supplies OMOP models and query-building primitives that applications
can use in those definitions; it does not provide a second DDL or refresh API.

The supported deployment contract is PostgreSQL with unqualified materialized
views resolving to the `public` schema. Omit the `schema` argument when calling
the lifecycle methods. Qualified non-`public` schemas are not currently part of
the supported contract.

## Define a view over an OMOP query

Use the public `orm_loader.materialized_views` module. A definition states the
view name, its SQLAlchemy selectable, the complete logical row identity, any
dependencies, and any indexes that should be created with the view:

```python
import sqlalchemy as sa

from orm_loader.materialized_views import (
    MaterializedViewIndex,
    MaterializedViewMixin,
)


event_query = sa.select(
    events.c.person_id,
    events.c.event_id,
    events.c.event_date,
)


class ClinicalEventsMV(MaterializedViewMixin):
    __mv_name__ = "clinical_events"
    __mv_select__ = event_query
    __mv_logical_identity__ = ("person_id", "event_id")
    __mv_dependencies__ = ("measurement", "observation")
    __mv_indexes__ = (
        MaterializedViewIndex(
            name="clinical_events_identity_uq",
            columns=("person_id", "event_id"),
            unique=True,
        ),
    )
```

`__mv_logical_identity__` documents the complete grain and is validated against
the selectable, but it is not itself a database constraint. Test that identity
against representative data and declare a matching unique index when the view
must support concurrent refresh.

`__mv_dependencies__` records tables or materialized views that the definition
depends on. The registry owner decides which dependencies are managed views and
uses that metadata to determine refresh order.

## Create, refresh, and drop

The class methods accept either a SQLAlchemy `Engine` or `Connection`. Passing
an engine lets `orm-loader` manage the transaction. Passing a connection keeps
the operation inside the caller's transaction:

```python
ClinicalEventsMV.create_mv(engine)
ClinicalEventsMV.refresh_mv(engine)
ClinicalEventsMV.drop_mv(engine)

with engine.begin() as connection:
    ClinicalEventsMV.create_mv(connection)
```

`create_mv()` creates the view and its declared indexes as one operation.
Creation fails by default if the target already exists, keeping definition
drift visible. Use `if_not_exists=True` only when an idempotent no-op is the
application's deliberate deployment policy.

PostgreSQL requires a suitable unique index for
`refresh_mv(concurrently=True)`. `orm-loader` rejects a definition with no
declared unique index before execution, then lets PostgreSQL decide whether the
live database satisfies its concurrent-refresh prerequisites. A database
rejection is translated to `ConcurrentRefreshNotEligibleError`, preserving the
original exception as its cause.

Lifecycle failures carry operation and target context through
`MaterializationError`. A failed statement can leave a caller-managed
PostgreSQL transaction aborted, so roll that transaction back before issuing
more statements. An `engine.begin()` context rolls back automatically when the
exception leaves the context.

## Keep orchestration with the application

OMOP Alchemy can own a reusable OMOP query and document its logical grain. The
application that deploys materialized views owns:

* the registry of view classes;
* uniqueness checks against representative data;
* dependency and refresh order;
* replacement and rebuild policy; and
* command-line, migration, or scheduler integration.

For the cohort delivery stack, those application concerns belong in
`omop-constructs`. Use `resolve_mv_refresh_order()` and `refresh_all_mvs()` from
`orm_loader.materialized_views` rather than implementing another dependency
resolver or refresh loop.

Refer to the
[`orm-loader` materialized-view guide](https://australiancancerdatanetwork.github.io/orm-loader/tables/mat_view/)
for the complete API and backend behaviour.
