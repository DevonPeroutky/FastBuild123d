"""The memoizing wrapper installed over model-file functions, the `@no_cache`
opt-out, and the debug-trace formatting.

Tagging discipline (SPEC-caching §3): every *cacheable* call tags its result
with its key — including calls below the promotion threshold, or the provenance
chain severs. A *bypassed* call never tags: its key would be absent or a lie,
and a fabricated tag poisons every downstream key that folds it in.

Formatting is pure; the wrapper performs the logging side effect at its own
boundary, only when debug is enabled.
"""

import functools
import inspect
import logging
from time import perf_counter
from typing import Any, Callable, Mapping

from build123d.build_common import Builder

from fastbuild.cache.analysis import Cacheability
from fastbuild.cache.keys import (
    TAG_ATTR,
    CacheKey,
    EncodedArgs,
    display,
    encode_arguments,
    key_of,
)
from fastbuild.cache.store import Store
from fastbuild.errors import Error

log = logging.getLogger("fastbuild")

NO_KEY = "-" * 10


def no_cache(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Opt a model-file function out of caching. Contagious: every caller of a
    `no_cache` function runs uncached too (a caller's hit would skip it)."""
    fn._fb_no_cache = True  # type: ignore[attr-defined]
    return fn


def _builder_active() -> bool:
    return Builder._get_context() is not None


def _tag(result: Any, key: CacheKey) -> None:
    try:
        setattr(result, TAG_ATTR, key)
    except (AttributeError, TypeError):
        pass


def format_call(
    qualname: str, args_text: str, key_text: str, outcome: str, duration_s: float
) -> str:
    call = f"{qualname}({args_text})"
    return f"  cache  {call:<32} k={key_text}  {outcome:<6} {duration_s * 1000:>5.0f}ms"


def _raw_args_text(args: tuple, kwargs: Mapping[str, Any]) -> str:
    parts = [repr(a) for a in args]
    parts.extend(f"{k}={v!r}" for k, v in kwargs.items())
    text = ", ".join(parts)
    return text if len(text) <= 48 else text[:45] + "..."


def _encoded_args_text(encoded: EncodedArgs) -> str:
    text = ", ".join(display(value) for _, value in encoded)
    return text if len(text) <= 48 else text[:45] + "..."


def memoize(
    fn: Callable[..., Any],
    *,
    qualname: str,
    quality: Cacheability,
    store: Store,
    debug: bool = False,
) -> Callable[..., Any]:
    """Wrap one model-file function. Not-cacheable functions still get a thin
    wrapper so bypasses show up in stats and the debug trace."""
    signature = inspect.signature(fn)

    def trace(args_text: str, key_text: str, outcome: str, started: float) -> None:
        if debug:
            line = format_call(qualname, args_text, key_text, outcome, perf_counter() - started)
            log.debug("%s", line)

    if not quality.cacheable:
        reason = quality.reasons[0]

        @functools.wraps(fn)
        def bypassing(*args: Any, **kwargs: Any) -> Any:
            started = perf_counter()
            store.note_bypass()
            result = fn(*args, **kwargs)
            trace(_raw_args_text(args, kwargs), NO_KEY, f"BYPASS {reason}", started)
            return result

        bypassing.stage_count = len(quality.members)  # type: ignore[attr-defined]
        bypassing._fb_wrapper = True  # type: ignore[attr-defined]
        return bypassing

    warned_builder_context = False

    def keyed_arguments(arguments: Mapping[str, Any]) -> tuple[CacheKey, EncodedArgs] | Error:
        encoded = encode_arguments(arguments)
        if isinstance(encoded, Error):
            return encoded
        return key_of(qualname, quality.closure_hash, encoded), encoded

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        nonlocal warned_builder_context
        started = perf_counter()
        if _builder_active():
            store.note_bypass()
            if not warned_builder_context:
                warned_builder_context = True
                log.warning(
                    "fastbuild cache: '%s' called inside an active builder "
                    "context; running uncached (@fastbuild.no_cache silences this)",
                    qualname,
                )
            result = fn(*args, **kwargs)
            trace(_raw_args_text(args, kwargs), NO_KEY, "BYPASS builder context", started)
            return result
        try:
            bound = signature.bind(*args, **kwargs)
        except TypeError:
            return fn(*args, **kwargs)
        bound.apply_defaults()
        keyed = keyed_arguments(bound.arguments)
        if isinstance(keyed, Error):
            store.note_bypass()
            result = fn(*args, **kwargs)
            trace(_raw_args_text(args, kwargs), NO_KEY, f"BYPASS {keyed.message}", started)
            return result
        key, encoded = keyed
        cached = store.get_part(key)
        if cached is not None:
            _tag(cached, key)
            trace(_encoded_args_text(encoded), key.short, "HIT", started)
            return cached
        result = fn(*args, **kwargs)
        duration = perf_counter() - started
        _tag(result, key)
        store.put_part(key, result, duration)
        trace(_encoded_args_text(encoded), key.short, "MISS", started)
        return result

    wrapper.stage_count = len(quality.members)  # type: ignore[attr-defined]
    wrapper._fb_wrapper = True  # type: ignore[attr-defined]
    return wrapper
