"""Compatibility warning for the superseded mapped clinical-event prototype."""

from __future__ import annotations

import subprocess
import sys


def test_clinical_event_union_module_warns_on_direct_import():
    result = subprocess.run(
        [
            sys.executable,
            "-W",
            "always::DeprecationWarning",
            "-c",
            "import omop_alchemy.cdm.model.clinical.clinical_event_union",
        ],
        capture_output=True,
        check=True,
        text=True,
    )

    assert "canonical_event_union" in result.stderr
    assert "removed in omop-alchemy 2.0" in result.stderr
