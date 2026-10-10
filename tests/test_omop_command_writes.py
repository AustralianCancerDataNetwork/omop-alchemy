"""omop_command's writes= parameter controls whether a command registers
schema claims, independent of mode_label/dry_run. A genuinely read-only
command (backup-database, analyze-tables) must pass register_claims=False
to create_cdm_engines() even when invoked in its default "apply" mode, so it
can run against a legacy, unbaselined database.
"""

from __future__ import annotations

import types

import pytest
from oa_configurator import Role

from omop_alchemy.maintenance._cli_utils import omop_command


class _FakeResolved:
    schema_name = "omop"

    def roles_on_connection(self, engine):
        return (Role.PRIMARY, Role.VOCAB, Role.RESULTS)


class _FakeURL:
    @staticmethod
    def render_as_string(hide_password: bool = True) -> str:
        return "postgresql://fake"


class _FakeEngine:
    url = _FakeURL()
    disposed = False

    def dispose(self) -> None:
        self.disposed = True


@pytest.fixture
def _patched_config(monkeypatch):
    """Capture register_claims passed to create_cdm_engines(), without
    touching a real database."""
    captured: dict[str, object] = {}

    def _fake_get_cdm_context(database=None):
        pkg_config = types.SimpleNamespace(cdm_db="cdm_db", athena_source_path=None)
        return pkg_config, _FakeResolved()

    def _fake_create_cdm_engines(resolved, *, register_claims: bool = True):
        captured["register_claims"] = register_claims
        engine = _FakeEngine()
        return engine, engine

    monkeypatch.setattr("omop_alchemy.config.get_cdm_context", _fake_get_cdm_context)
    monkeypatch.setattr("omop_alchemy.config.create_cdm_engines", _fake_create_cdm_engines)
    return captured


def test_writes_false_never_registers_claims_even_in_apply_mode(_patched_config):
    @omop_command("fake-read-only-command", writes=False)
    def _command(conn):
        return "ok"

    assert _command() == "ok"
    assert _patched_config["register_claims"] is False


def test_writes_true_is_the_default_and_registers_claims_in_apply_mode(_patched_config):
    @omop_command("fake-writing-command")
    def _command(conn):
        return "ok"

    assert _command() == "ok"
    assert _patched_config["register_claims"] is True


def test_writes_false_combines_with_dry_run_mode_label(_patched_config):
    """The mode_label/dry_run denylist still applies on top of writes=False;
    this just proves writes=False doesn't accidentally re-enable claims for
    an inspect-labelled command."""

    @omop_command("fake-inspect-command", writes=False, mode_label="inspect")
    def _command(conn):
        return "ok"

    assert _command() == "ok"
    assert _patched_config["register_claims"] is False


def test_writes_true_with_dry_run_mode_label_still_skips_claims(_patched_config):
    """writes=True (the default) still defers to the existing dry-run/inspect
    denylist for a normally-writing command invoked in one of those modes."""

    @omop_command("fake-command", mode_label="inspect")
    def _command(conn):
        return "ok"

    assert _command() == "ok"
    assert _patched_config["register_claims"] is False
