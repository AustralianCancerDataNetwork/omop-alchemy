"""Reference joins derive mapper identity while preserving deferred loading."""

import pytest
import sqlalchemy as sa
import sqlalchemy.orm as so

from omop_alchemy.cdm.base import ReferenceContext


@pytest.fixture(params=[None, "identifier"])
def reference_models(request):
    class LocalBase(so.DeclarativeBase):
        pass

    # Declare the source before its target to exercise deferred resolution.
    class Source(LocalBase):
        __tablename__ = "source"
        id: so.Mapped[int] = so.mapped_column(primary_key=True)
        target_identifier: so.Mapped[str]
        reference = ReferenceContext._reference_relationship(
            target="Target",
            local_fk="target_identifier",
            remote_pk=request.param,
        )

    class Target(LocalBase):
        __tablename__ = "target"
        identifier: so.Mapped[str] = so.mapped_column("physical_key", primary_key=True)
        alternative: so.Mapped[str]

    try:
        yield LocalBase, Source, Target
    finally:
        LocalBase.registry.dispose()


def test_reference_join_uses_mapped_attribute_and_batches_loading(reference_models):
    base, source, target = reference_models
    relationship = sa.inspect(source).relationships.reference
    assert relationship.primaryjoin.compare(
        source.target_identifier == target.identifier
    )
    assert relationship.viewonly
    assert relationship.lazy == "selectin"
    assert not relationship.uselist

    engine = sa.create_engine("sqlite://")
    base.metadata.create_all(engine)
    with so.Session(engine) as session:
        session.add_all(
            [
                target(identifier="pk-1", alternative="other-1"),
                source(id=1, target_identifier="pk-1"),
                source(id=2, target_identifier="pk-1"),
            ]
        )
        session.commit()
    statements = []
    sa.event.listen(
        engine, "before_cursor_execute", lambda *args: statements.append(args[2])
    )
    try:
        with so.Session(engine) as session:
            rows = session.scalars(sa.select(source).order_by(source.id)).all()
            assert [row.reference.identifier for row in rows] == ["pk-1", "pk-1"]
            assert len(statements) == 2
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "reference_models", ["alternative", "physical_key"], indirect=True
)
def test_wrong_legacy_reference_key_is_rejected(reference_models):
    base, _, _ = reference_models
    with pytest.raises(ValueError, match="primary key is 'identifier', not"):
        base.registry.configure()


def test_missing_reference_target_is_rejected(reference_models):
    base, _, _ = reference_models

    class MissingSource(base):
        __tablename__ = "missing_source"
        id: so.Mapped[int] = so.mapped_column(primary_key=True)
        target_id: so.Mapped[int]
        reference = ReferenceContext._reference_relationship(
            target="Missing",
            local_fk="target_id",
        )

    with pytest.raises(
        ValueError, match="reference target 'Missing' is missing or ambiguous"
    ):
        base.registry.configure()


def test_composite_reference_key_is_rejected(reference_models):
    base, _, _ = reference_models

    class CompositeSource(base):
        __tablename__ = "composite_source"
        id: so.Mapped[int] = so.mapped_column(primary_key=True)
        target_id: so.Mapped[int]
        reference = ReferenceContext._reference_relationship(
            target="Composite",
            local_fk="target_id",
        )

    class Composite(base):
        __tablename__ = "composite"
        id: so.Mapped[int] = so.mapped_column(primary_key=True)
        other_id: so.Mapped[int] = so.mapped_column(primary_key=True)

    with pytest.raises(ValueError, match="exactly one mapped primary key"):
        base.registry.configure()
