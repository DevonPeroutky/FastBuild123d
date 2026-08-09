"""In-memory cache store: one LRU of parts, deepcopy discipline.

Mutable by design and isolated here. Threading invariant: one mutator thread
(the server's single build/reload worker); the event loop only reads `stats()`
snapshots, which may mix pre/post counters mid-build — fine for a footer.

`deepcopy` on insert and on hit is deliberate: build123d's `__copy__` shares
the underlying TopoDS_TShape, so only deepcopy keeps stored geometry immune to
caller mutation, and it re-links joints.

Meshes are not stored here — they are keyed by the same provenance digests in
`ocp_tessellate`'s own LRU (SPEC-mesh-caching §3.5).
"""

import copy
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from fastbuild.cache.keys import CacheKey


@dataclass(frozen=True)
class FrozenStats:
    hits: int
    misses: int
    bypasses: int
    entries: int


class Store:
    def __init__(
        self, *, max_entries: int = 128, promote_threshold_s: float = 0.010
    ) -> None:
        self._parts: OrderedDict[CacheKey, Any] = OrderedDict()
        self._max_entries = max_entries
        self._threshold_s = promote_threshold_s
        self._hits = 0
        self._misses = 0
        self._bypasses = 0

    def get_part(self, key: CacheKey) -> Any | None:
        """Membership decides hit/miss, not the stored value — a model function
        may legitimately return None."""
        if key not in self._parts:
            self._misses += 1
            return None
        self._hits += 1
        self._parts.move_to_end(key)
        return copy.deepcopy(self._parts[key])

    def put_part(self, key: CacheKey, part: Any, duration_s: float) -> None:
        """Store a copy — but only for calls worth caching: below the promotion
        threshold the copy costs more than the rebuild (SPEC-caching §3)."""
        if duration_s < self._threshold_s:
            return
        self._parts[key] = copy.deepcopy(part)
        self._parts.move_to_end(key)
        while len(self._parts) > self._max_entries:
            self._parts.popitem(last=False)

    def note_bypass(self) -> None:
        self._bypasses += 1

    def stats(self) -> FrozenStats:
        return FrozenStats(
            hits=self._hits,
            misses=self._misses,
            bypasses=self._bypasses,
            entries=len(self._parts),
        )


def build_outcome(before: FrozenStats, after: FrozenStats) -> str:
    """Which cache behavior dominated the build between two snapshots."""
    if after.misses > before.misses:
        return "miss"
    if after.bypasses > before.bypasses:
        return "bypass"
    return "hit"
