"""Shared error contracts for toolkit model validation."""

from __future__ import annotations

from typing import ClassVar


class UnsupportedModelError(TypeError):
    """Base error carrying the model and reason for unsupported-model failures."""

    model_kind: ClassVar[str] = "model"

    def __init__(
        self,
        model: object | None,
        reason: str,
        *,
        message: str | None = None,
    ) -> None:
        self.model = model
        self.reason = reason
        if message is None:
            name = getattr(model, "__name__", repr(model))
            message = f"{name} is not a supported {self.model_kind}: {reason}"
        super().__init__(message)
