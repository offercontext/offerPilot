"""Request-local Readiness identity frozen before detached queue admission."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from .readiness import ReadinessContextBinding


@dataclass(frozen=True)
class FrozenReadinessSelection:
    # The envelope distinguishes an explicitly frozen absent selection from a
    # legacy call that still needs to capture its selection before executing.
    binding: ReadinessContextBinding | None


_selection: ContextVar[FrozenReadinessSelection | None] = ContextVar("readiness_admission", default=None)


def current_frozen_readiness() -> FrozenReadinessSelection | None:
    return _selection.get()


@contextmanager
def frozen_readiness_scope(binding: ReadinessContextBinding | None) -> Iterator[None]:
    token = _selection.set(FrozenReadinessSelection(binding))
    try:
        yield
    finally:
        _selection.reset(token)
