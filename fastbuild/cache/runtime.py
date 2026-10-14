"""Execution-scoped geometry cache policy, owned by one viewer."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


class CacheRuntime:
    def __init__(self) -> None:
        self._disabled = ContextVar('fastbuild_cache_disabled', default=False)

    @property
    def is_disabled(self) -> bool:
        return self._disabled.get()

    @contextmanager
    def disabled(self) -> Iterator[None]:
        token = self._disabled.set(True)
        try:
            yield
        finally:
            self._disabled.reset(token)
