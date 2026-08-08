"""Build scheduling: one coalescing queue plus a trailing-edge drag debouncer.

At most one build runs at a time and at most one is pending; a newer request
replaces the pending one (newest wins). Drag events fire the queue at most
once per debounce window, reading the freshest state at fire time.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping


@dataclass(frozen=True)
class BuildRequest:
    part: str
    values: Mapping[str, Any]


class BuildQueue:
    def __init__(self, execute: Callable[[BuildRequest], Awaitable[None]]) -> None:
        self._execute = execute
        self._pending: BuildRequest | None = None
        self._worker: asyncio.Task | None = None

    @property
    def busy(self) -> bool:
        return self._worker is not None and not self._worker.done()

    def submit(self, request: BuildRequest) -> None:
        self._pending = request
        if not self.busy:
            self._worker = asyncio.get_running_loop().create_task(self._drain())

    async def _drain(self) -> None:
        while self._pending is not None:
            request = self._pending
            self._pending = None
            await self._execute(request)

    async def join(self) -> None:
        while self._worker is not None and not self._worker.done():
            await asyncio.shield(self._worker)


class TrailingDebounce:
    """Coalesces a burst of touches into one action per window, trailing edge."""

    def __init__(self, delay_s: float, action: Callable[[], None]) -> None:
        # window length
        self._delay_s = delay_s
        # What to do when window closes
        self._action = action
        self._handle: asyncio.TimerHandle | None = None

    def touch(self) -> None:
        """User drag(s) slider, start debounce window"""
        if self._handle is None:
            self._handle = asyncio.get_running_loop().call_later(
                self._delay_s, self._fire
            )

    def flush(self) -> None:
        """User released the slider. Immediatly close window and call action"""
        self.cancel()
        self._action()

    def cancel(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None

    def _fire(self) -> None:
        """Run the action and close the window"""
        self._handle = None
        self._action()
