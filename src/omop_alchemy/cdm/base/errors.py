"""Shared error contracts for CDM model metadata validation."""

from __future__ import annotations

from typing import ClassVar


class UnsupportedModelError(TypeError):
    """Base error carrying the model and reason for unsupported-model failures."""

    model_kind: ClassVar[str] = "model"

    def __init__(self, model: object, reason: str) -> None:
        self.model = model
        self.reason = reason
        name = getattr(model, "__name__", repr(model))
        super().__init__(f"{name} is not a supported {self.model_kind}: {reason}")
