import sqlalchemy as sa
import sqlalchemy.orm as so


class ConceptValidationMixin:
    """
    Structural validation for concept-bearing columns.

    A concept-bearing column is defined as:
      - column name ends with '_concept_id'
      - column name does not contain 'source' (excludes *_source_concept_id)

    No type check is performed; matching is by column-name pattern only.

    Works for:
      - ORM mapped tables
      - materialised views
      - Core selectables
    """

    __abstract__ = True

    @classmethod
    def concept_id_columns(cls) -> dict[str, sa.ColumnElement]:
        """
        Return all columns that look like concept_id columns.
        """
        mapper = sa.inspect(cls)

        if mapper and hasattr(mapper, "columns"):
            cols = mapper.columns
        else:
            raise TypeError(f"{cls.__name__} is not inspectable")

        return {
            c.key: c
            for c in cols
            if c.key and c.key.endswith("_concept_id") and 'source' not in c.key
        }


    @classmethod
    def get_queryable_table(
        cls,
        session: so.Session,
    ) -> sa.sql.FromClause:
        """
        Return a selectable suitable for querying concept IDs.

        Defaults to the mapped table, but allows override for
        staging tables or materialised views.
        """
        mapper = sa.inspect(cls)

        # ORM-mapped table or MV
        if mapper and hasattr(mapper, "local_table"):
            return mapper.local_table

        raise TypeError(f"Cannot resolve queryable table for {cls.__name__}")


    @classmethod
    def _non_standard_concepts_for_column(
        cls,
        *,
        table: sa.sql.FromClause,
        col: sa.ColumnElement,
        domain_id: str | None = None,
        vocabulary_id: str | None = None,
        limit: int | None = None,
    ) -> sa.Select:
        """
        Build a query that returns DISTINCT concept_ids from a single
        *_concept_id column that do NOT satisfy the "standard concept"
        constraint (or do not exist).

        Parameters
        ----------
        table
            Queryable table or selectable backing the ORM class or MV.
        col
            ColumnElement corresponding to a *_concept_id column.
        domain_id
            Optional domain constraint.
        vocabulary_id
            Optional vocabulary constraint.
        limit
            Optional LIMIT on returned violations.

        Returns
        -------
        sqlalchemy.Select
            A SELECT returning a single column: the violating concept_id.
        """
        # imported here rather than at module scope to avoid a circular import
        from omop_alchemy.cdm.model.vocabulary.concept import Concept

        # Base join condition: concept_id match
        join_cond = Concept.concept_id == col

        # Optional semantic constraints belong in the JOIN,
        # so missing concepts are still caught
        if domain_id:
            join_cond = sa.and_(
                join_cond,
                Concept.domain_id == domain_id,
            )

        if vocabulary_id:
            join_cond = sa.and_(
                join_cond,
                Concept.vocabulary_id == vocabulary_id,
            )

        from_clause = sa.outerjoin(
            table,
            Concept,
            join_cond,
        )


        stmt = (
            sa.select(sa.distinct(col))
            .select_from(from_clause)
            .where(
                col.is_not(None),
                sa.or_(
                    # outer join left no concept row at all
                    Concept.concept_id.is_(None),
                    # Concept owns the definition of standard-ness, including
                    # tolerance for blank and whitespace-only flag values.
                    # is_not(True) rather than not_(): the expression is NULL
                    # for a NULL or blank flag, and NOT NULL would not match.
                    Concept.is_standard_expr().is_not(True),
                ),
            )
        )

        if limit is not None:
            stmt = stmt.limit(limit)

        return stmt


    @classmethod
    def _non_standard_concepts_across_databases(
        cls,
        session: so.Session,
        *,
        table: sa.sql.FromClause,
        col: sa.ColumnElement,
        domain_id: str | None,
        vocabulary_id: str | None,
        limit: int | None,
    ) -> set[int]:
        """Violating concept IDs when *table* and ``Concept`` are on two databases.

        Reads the distinct referenced IDs on *table*'s side, keeps those that
        resolve to a matching standard concept on the vocabulary side, and
        returns the rest. Same result as the single-join form.
        """
        from omop_alchemy.cdm.model.vocabulary.concept import Concept
        from omop_alchemy.cross_database import filter_by_keys

        referenced = sa.select(sa.distinct(col)).select_from(table).where(col.is_not(None))
        conforming = sa.select(Concept.concept_id).where(
            filter_by_keys(Concept.concept_id, keys_select=referenced, session=session),
            Concept.is_standard_expr().is_(True),
        )
        if domain_id:
            conforming = conforming.where(Concept.domain_id == domain_id)
        if vocabulary_id:
            conforming = conforming.where(Concept.vocabulary_id == vocabulary_id)

        used = {int(cid) for cid in session.scalars(referenced)}
        bad = sorted(used - {int(cid) for cid in session.scalars(conforming)})
        return set(bad if limit is None else bad[:limit])

    @classmethod
    def referenced_concept_violations(
        cls,
        session: so.Session,
        *,
        domain_id: str | None = None,
        vocabulary_id: str | None = None,
        limit: int | None = None,
    ) -> dict[str, set[int]]:
        """
        Return non-standard referenced concept IDs grouped by column name.

        One join per column when *session* sends this table and ``Concept``
        to one engine, two keyed reads per column when they are on separate
        databases.
        """
        from omop_alchemy.cdm.model.vocabulary.concept import Concept

        table = cls.get_queryable_table(session)
        cols = cls.concept_id_columns()
        colocated = (
            session.get_bind(clause=sa.select(table)).engine
            is session.get_bind(Concept).engine
        )

        violations: dict[str, set[int]] = {}

        for col_name, col in cols.items():
            if colocated:
                stmt = cls._non_standard_concepts_for_column(
                    table=table,
                    col=col,
                    domain_id=domain_id,
                    vocabulary_id=vocabulary_id,
                    limit=limit,
                )
                bad_ids = {int(cid) for (cid,) in session.execute(stmt)}
            else:
                bad_ids = cls._non_standard_concepts_across_databases(
                    session,
                    table=table,
                    col=col,
                    domain_id=domain_id,
                    vocabulary_id=vocabulary_id,
                    limit=limit,
                )

            if bad_ids:
                violations[col_name] = bad_ids

        return violations
