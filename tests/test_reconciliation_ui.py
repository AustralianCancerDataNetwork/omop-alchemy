"""Nonblocking schema notices stay visible without being labelled drift."""

from io import StringIO

import pytest
from rich.console import Console

from omop_alchemy.maintenance._cli_utils import Status
from omop_alchemy.maintenance.cli_schema_reconcile import ReconciliationIssue
from omop_alchemy.maintenance.tables import TableCategory
from omop_alchemy.maintenance.ui import render_reconciliation_issues


def _issue(status):
    return ReconciliationIssue(
        table_name="episode", category=TableCategory.STRUCTURAL,
        component="foreign_key_enforcement", object_name="episode_parent",
        status=status, expected="enforced", actual="notice detail", detail="retained diagnostic",
    )


def _render(issues):
    output = StringIO()
    Console(file=output, width=200, color_system=None).print(render_reconciliation_issues(issues))
    return output.getvalue()


@pytest.mark.parametrize("status", [Status.LIMITED, Status.RENAMED])
def test_nonblocking_notices_do_not_replace_the_no_drift_panel(status):
    text = _render([_issue(status)])
    assert "No Drift" in text
    assert "Schema notices" in text
    assert "Differences" not in text
    assert "retained diagnostic" in text


def test_blocking_issues_still_show_differences_and_preserve_notices():
    text = _render([_issue(Status.LIMITED), _issue(Status.MISMATCH)])
    assert "Differences" in text
    assert "No Drift" not in text
    assert "limited" in text.lower() and "mismatch" in text.lower()


def test_an_empty_report_shows_only_the_no_drift_panel():
    text = _render([])
    assert "No Drift" in text
    assert "Schema notices" not in text
