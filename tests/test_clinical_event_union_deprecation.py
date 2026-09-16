"""Compatibility warning for the superseded mapped clinical-event prototype."""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_clinical_event_union_module_warns_on_direct_import():
    result = subprocess.run(
        [
            sys.executable,
            "-W",
            "always::DeprecationWarning",
            "-c",
            textwrap.dedent(
                """
                import warnings
                import sqlalchemy as sa
                from sqlalchemy.dialects import sqlite
                from omop_alchemy.cdm.model.clinical.clinical_event_union import (
                    ClinicalEventView, clinical_event_union,
                )

                assert "canonical_event_union" in ClinicalEventView.__deprecated__
                assert "removed in omop-alchemy 2.0" in ClinicalEventView.__deprecated__
                with warnings.catch_warnings(record=True) as emitted:
                    warnings.simplefilter("always", DeprecationWarning)
                    mapper = sa.inspect(ClinicalEventView)
                    assert mapper.local_table is clinical_event_union
                    assert [column.key for column in mapper.primary_key] == [
                        "domain", "event_id",
                    ]
                    row = ClinicalEventView(domain="condition", event_id=7)
                    assert sa.inspect(row).mapper is mapper
                    class SpecializedEvent(ClinicalEventView):
                        pass
                    assert sa.inspect(SpecializedEvent).inherits is mapper
                    sa.select(ClinicalEventView).compile(dialect=sqlite.dialect())
                    assert not emitted
                """
            ),
        ],
        capture_output=True,
        check=True,
        text=True,
    )

    assert "canonical_event_union" in result.stderr
    assert "removed in omop-alchemy 2.0" in result.stderr
