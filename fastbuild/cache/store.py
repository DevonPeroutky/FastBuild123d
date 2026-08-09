"""In-memory cache store: one LRU of part+mesh entries, deepcopy discipline.

Mutable by design and isolated here. Threading invariant: one mutator thread
(the server's single build/reload worker); the event loop only reads `stats()`
snapshots, which may mix pre/post counters mid-build — fine for a footer.

`deepcopy` on insert and on hit is deliberate: build123d's `__copy__` shares
the underlying TopoDS_TShape, so only deepcopy keeps stored geometry immune to
caller mutation, and it re-links joints.
"""

import copy
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Mapping

from fastbuild.cache.keys import CacheKey


@dataclass(frozen=True)
class FrozenStats:
    hits: int
    misses: int
    bypasses: int
    mesh_hits: int
    entries: int


@dataclass
class _Entry:
    part: Any
    mesh: Mapping[str, Any] | None = None


class Store:
    def __init__(
        self, *, max_entries: int = 128, promote_threshold_s: float = 0.010
    ) -> None:
        self._entries: OrderedDict[CacheKey, _Entry] = OrderedDict()
        self._max_entries = max_entries
        self._threshold_s = promote_threshold_s
        self._hits = 0
        self._misses = 0
        self._bypasses = 0
        self._mesh_hits = 0

    def get_part(self, key: CacheKey) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            self._misses += 1
            return None
        self._hits += 1
        self._entries.move_to_end(key)
        return copy.deepcopy(entry.part)

    def put_part(self, key: CacheKey, part: Any, duration_s: float) -> None:
        """Store a copy — but only for calls worth caching: below the promotion
        threshold the copy costs more than the rebuild (SPEC-caching §3)."""
        if duration_s < self._threshold_s:
            return
        self._entries[key] = _Entry(part=copy.deepcopy(part))
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def get_mesh(self, key: CacheKey) -> Mapping[str, Any] | None:
        entry = self._entries.get(key)
        if entry is None or entry.mesh is None:
            return None
        self._mesh_hits += 1
        self._entries.move_to_end(key)
        return entry.mesh

    def put_mesh(self, key: CacheKey, mesh: Mapping[str, Any]) -> None:
        entry = self._entries.get(key)
        if entry is not None:
            entry.mesh = mesh

    def note_bypass(self) -> None:
        self._bypasses += 1

    def stats(self) -> FrozenStats:
        return FrozenStats(
            hits=self._hits,
            misses=self._misses,
            bypasses=self._bypasses,
            mesh_hits=self._mesh_hits,
            entries=len(self._entries),
        )


def build_outcome(before: FrozenStats, after: FrozenStats) -> str:
    """Which cache behavior dominated the build between two snapshots."""
    if after.misses > before.misses:
        return "miss"
    if after.bypasses > before.bypasses:
        return "bypass"
    return "hit"
