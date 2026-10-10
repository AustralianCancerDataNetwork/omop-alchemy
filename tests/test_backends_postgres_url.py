"""PostgreSQL client URI construction without a live server."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from omop_alchemy.backends.postgres import _libpq_connection_uri


def test_libpq_uri_preserves_colon_in_database_name():
    psycopg = pytest.importorskip("psycopg")
    url = sa.engine.URL.create(
        "postgresql+psycopg",
        username="test_user",
        password="test_password",
        host="review-db.invalid",
        port=55432,
        database="analytics:staging",
    )

    uri = _libpq_connection_uri(url)

    assert psycopg.conninfo.conninfo_to_dict(uri)["dbname"] == "analytics:staging"
