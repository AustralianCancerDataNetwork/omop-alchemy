"""Tests that omop_alchemy stays importable without optional dependencies.
"""

from __future__ import annotations

import subprocess
import sys


def _run_blocked(import_statement: str, *, blocked_module: str = "duckdb_sqlalchemy") -> subprocess.CompletedProcess[str]:
    code = f"import sys; sys.modules['{blocked_module}'] = None; {import_statement}"
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)


def test_omop_alchemy_importable_without_duckdb_sqlalchemy():
    """omop_alchemy imports cleanly when duckdb-sqlalchemy is not installed."""
    result = _run_blocked("import omop_alchemy")
    assert result.returncode == 0, result.stderr


def test_maintenance_cli_importable_without_duckdb_sqlalchemy():
    """omop_alchemy.maintenance.cli imports cleanly when duckdb-sqlalchemy is not installed."""
    result = _run_blocked("import omop_alchemy.maintenance.cli")
    assert result.returncode == 0, result.stderr


def test_omop_alchemy_importable_without_psycopg():
    """omop_alchemy imports cleanly when psycopg is not installed."""
    result = _run_blocked("import omop_alchemy", blocked_module="psycopg")
    assert result.returncode == 0, result.stderr


def test_maintenance_cli_importable_without_psycopg():
    """omop_alchemy.maintenance.cli imports cleanly when psycopg is not installed."""
    result = _run_blocked("import omop_alchemy.maintenance.cli", blocked_module="psycopg")
    assert result.returncode == 0, result.stderr
