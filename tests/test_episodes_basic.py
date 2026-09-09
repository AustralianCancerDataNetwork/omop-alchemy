from omop_alchemy.cdm.base import ModifierFieldConcepts
from omop_alchemy.cdm.model.structural import (
    EpisodeView,
    Episode_Event,
    Episode_EventView,
    clear_episode_event_target_class_cache,
)
from omop_alchemy.toolkit.episodes.handling import (
    DEFAULT_EPISODE_OPEN_END_FALLBACK_DAYS,
    ResolvedEpisodeEvent,
    ResolvedEpisodeEventMixin,
    episode_attachment_window,
)
import datetime
import sqlalchemy as sa


class DiagnosticEpisode(ResolvedEpisodeEventMixin, EpisodeView):
    """Non-oncology episode view used to verify the generic extension point."""

    __tablename__ = "episode"
    __mapper_args__ = {"concrete": False}


def test_episode_view_expected_domains():
    """Test episode view expected domains."""
    cls = EpisodeView

    assert "episode_concept_id" in cls.__expected_domains__
    assert "episode_object_concept_id" in cls.__expected_domains__
    assert "episode_type_concept_id" in cls.__expected_domains__

    assert cls.__expected_domains__["episode_concept_id"].domains == frozenset(
        {"Episode"}
    )


def test_episode_reference_context(session):
    """Test episode reference context."""
    ep = session.query(EpisodeView).first()
    assert ep is not None

    # ReferenceContext relationships
    assert ep.person is not None
    assert ep.episode_concept is not None
    assert ep.episode_type_concept is not None


def test_episode_has_episode_events(session):
    """Test episode has episode events."""
    ep = session.query(EpisodeView).filter(EpisodeView.episode_events.any()).first()

    assert ep is not None
    assert len(ep.episode_events) > 0


def test_resolved_episode_event_mixin_targets_diagnostic_rows(session):
    episode = (
        session.query(DiagnosticEpisode)
        .filter(DiagnosticEpisode.episode_events.any())
        .first()
    )

    assert episode is not None
    assert episode.episode_events
    assert all(
        isinstance(episode_event, ResolvedEpisodeEvent)
        for episode_event in episode.episode_events
    )


def test_episode_event_resolves_target(session):
    """Test episode event resolves target."""
    ep = session.query(EpisodeView).filter(EpisodeView.episode_events.any()).first()

    ee = ep.episode_events[0]
    target = ee.resolved_event

    assert target is not None
    # confirm that resolution gives us back a Condition_Occurrence/Drug_Exposure etc. object, not the original episode_event
    assert not isinstance(target, Episode_Event)
    assert hasattr(target, "__table__")
    assert hasattr(target, "person_id")

    assert target.__tablename__ == ee.event_table

    pk_cols = [c.name for c in sa.inspect(target.__class__).primary_key]
    assert len(pk_cols) == 1
    assert getattr(target, pk_cols[0]) == ee.event_id

    col = ee.resolved_event_id_column
    assert col is not None
    assert getattr(target, col) == ee.event_id


def test_episode_event_resolution_has_no_diagnostics_for_valid_target(session):
    """Valid episode_event links resolve without advisory issues."""
    ee = (
        session.query(EpisodeView)
        .filter(EpisodeView.episode_events.any())
        .first()
        .episode_events[0]
    )
    resolved = session.get(
        ResolvedEpisodeEvent,
        (ee.episode_id, ee.event_id, ee.episode_event_field_concept_id),
    )

    assert resolved.resolved_event is not None
    assert resolved.event_resolution_diagnostics == []


def test_episode_event_resolution_reports_unrecognized_field_concept(session):
    """A field concept outside ModifierFieldConcepts is surfaced separately."""
    ee = Episode_Event(
        episode_id=101,
        event_id=998,
        episode_event_field_concept_id=201826,
    )
    session.add(ee)
    session.flush()
    session.expire_all()

    loaded = session.get(ResolvedEpisodeEvent, (101, 998, 201826))
    diagnostics = loaded.event_resolution_diagnostics

    assert loaded.resolved_event is None
    assert len(diagnostics) == 1
    assert diagnostics[0].kind == "unrecognized_field_concept"


def test_episode_event_resolution_reports_unmapped_known_field_concept(
    session,
    monkeypatch,
):
    """Known field concepts can be valid data even before this ORM maps them."""
    ee = (
        session.query(EpisodeView)
        .filter(EpisodeView.episode_events.any())
        .first()
        .episode_events[0]
    )
    resolved = session.get(
        ResolvedEpisodeEvent,
        (ee.episode_id, ee.event_id, ee.episode_event_field_concept_id),
    )
    monkeypatch.setattr(
        type(resolved),
        "resolved_event_target_classes",
        classmethod(lambda cls: {}),
    )

    diagnostics = resolved.event_resolution_diagnostics

    assert resolved.resolved_event is None
    assert len(diagnostics) == 1
    assert diagnostics[0].kind == "unmapped_field_concept"


def test_episode_event_resolution_reports_dangling_target(session):
    """Recognized field concepts with missing target rows are true dangling links."""
    ee = Episode_Event(
        episode_id=101,
        event_id=999,
        episode_event_field_concept_id=ModifierFieldConcepts.CONDITION_OCCURRENCE,
    )
    session.add(ee)
    session.flush()
    session.expire_all()

    loaded = session.get(
        ResolvedEpisodeEvent,
        (101, 999, ModifierFieldConcepts.CONDITION_OCCURRENCE),
    )
    diagnostics = loaded.event_resolution_diagnostics

    assert loaded.resolved_event is None
    assert len(diagnostics) == 1
    assert diagnostics[0].kind == "dangling_event"


def test_episode_event_target_classes_are_isolated_from_caller_mutation():
    """Resolver overrides cannot mutate the stable Core target registry."""
    first = Episode_EventView.resolved_event_target_classes()
    second = Episode_EventView.resolved_event_target_classes()

    assert first is not second
    first.clear()
    clear_episode_event_target_class_cache()
    third = Episode_EventView.resolved_event_target_classes()

    assert third == second


def test_episode_view_events_property(session):
    """Test episode view events property."""
    ep = session.query(EpisodeView).filter(EpisodeView.episode_events.any()).first()

    events = ep.events

    assert isinstance(events, list)
    assert len(events) > 0

    for target in events:
        assert target is not None
        # confirm that resolution gives us back a Condition_Occurrence/Drug_Exposure etc. object, not the original episode_event
        assert not isinstance(target, Episode_Event)
        assert hasattr(target, "__table__")
        assert hasattr(target, "person_id")


def test_episode_parent_relationship(session):
    """Test episode parent relationship."""
    child = (
        session.query(EpisodeView)
        .filter(EpisodeView.episode_parent_id.isnot(None))
        .first()
    )

    if child:
        assert child.parent_episode is not None
        assert child.parent_episode.episode_id == child.episode_parent_id


def test_episode_children_relationship_preserves_episode_view(session):
    """Child navigation returns the concrete episode view being queried."""
    parent = session.get(EpisodeView, 100)

    assert parent is not None
    assert [child.episode_id for child in parent.children] == [101]
    assert all(isinstance(child, EpisodeView) for child in parent.children)


def test_episode_date_bounds(session):
    """Test episode date bounds."""
    ep = session.query(EpisodeView).first()

    if ep.episode_end_date:
        assert ep.episode_start_date <= ep.episode_end_date


class _WindowStub:
    def __init__(self, start, end):
        self.episode_start_date = start
        self.episode_end_date = end


def test_attachment_window_honours_an_explicit_end_date():
    """A long but closed episode keeps its own end date, not the open-end cap."""
    episode = _WindowStub(datetime.date(2020, 1, 1), datetime.date(2023, 6, 30))

    window_start, window_end = episode_attachment_window(episode)

    assert window_start == datetime.date(2019, 10, 3)
    assert window_end == datetime.date(2023, 6, 30)


def test_attachment_window_caps_open_ended_episodes():
    episode = _WindowStub(datetime.date(2020, 1, 1), None)

    _, window_end = episode_attachment_window(episode)

    assert window_end == datetime.date(2020, 1, 1) + datetime.timedelta(
        days=DEFAULT_EPISODE_OPEN_END_FALLBACK_DAYS
    )
