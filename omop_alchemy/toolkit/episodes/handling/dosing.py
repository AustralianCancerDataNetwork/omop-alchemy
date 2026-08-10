from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DoseEvaluability:
    """
    Whether a dose-like summary can be interpreted as a dose quantity.

    Drug exposure rows often carry source units and quantities without enough
    normalization to compare across agents or regimens. Domain-specific dosing
    modules should use this as a small shared vocabulary instead of silently
    treating missing or mixed units as comparable.
    """

    evaluable: bool
    reason: Optional[str] = None
