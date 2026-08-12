"""Attachment guard for governed concept-set membership.

Governed membership is a question about the vocabulary, so answering it needs a
session.  A detached instance cannot answer, and reporting ``False`` would
render "cannot tell" as "not a member" — which propagates: modality
classification would return ``UNKNOWN`` or ``SACT`` for an episode whose RT
procedures simply could not be checked, with no error and a plausible-looking
result.

Failing loudly is the right trade in a clinical dataset.  The class-level
expression form needs no session and stays available for SQL filtering.
"""

from __future__ import annotations

import sqlalchemy.orm as so


def require_session(instance: object) -> so.Session:
    """Return ``instance``'s session, or raise explaining what to do instead."""
    session = so.object_session(instance)
    if session is None:
        raise RuntimeError(
            f"{type(instance).__name__} is not attached to a Session, so governed "
            "concept-set membership cannot be determined. Load it through a live "
            "Session, or use the class-level expression form in a SQL filter."
        )
    return session
