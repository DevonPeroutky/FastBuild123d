"""Provenance-keyed memoization for model-file functions (SPEC-caching Phase 1).

`keys` and `analysis` are pure; `store` isolates the mutable cache; `wrap`
builds the memoizing wrappers that `harness.install_cache` installs.
"""

from fastbuild.cache.keys import CacheKey
from fastbuild.cache.store import FrozenStats, Store
from fastbuild.cache.wrap import no_cache

__all__ = ["CacheKey", "FrozenStats", "Store", "no_cache"]
