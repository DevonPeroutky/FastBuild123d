"""Load a model file: source → AST → compile → exec into a fresh namespace.

The harness (not `import`) owns the exact source text, which is what the
caching layer keys on (SPEC-caching §3.1). Load failures are returned, not
raised — a broken file mid-edit is an expected condition.
"""

import ast
import hashlib
import traceback
from dataclasses import dataclass, replace
from pathlib import Path
from types import FunctionType, MappingProxyType
from typing import Any, Iterator, Mapping

from fastbuild.cache.analysis import analyze_functions, resolve
from fastbuild.cache.store import Store
from fastbuild.cache.wrap import memoize
from fastbuild.errors import Error
from fastbuild.registry import App, PartDef


@dataclass(frozen=True)
class LoadedModel:
    path: Path
    source: str
    app: App
    function_hashes: Mapping[str, str]
    namespace: Mapping[str, Any]
    parts: Mapping[str, PartDef]


FunctionDef = ast.FunctionDef | ast.AsyncFunctionDef


def _qualified_functions(node: ast.AST, prefix: str) -> Iterator[tuple[str, FunctionDef]]:
    """Every function in the tree paired with its `__qualname__`, so a nested
    helper never collides with a same-named one elsewhere in the file."""
    for child in ast.iter_child_nodes(node):
        match child:
            case ast.FunctionDef() | ast.AsyncFunctionDef():
                qualname = prefix + child.name
                yield qualname, child
                yield from _qualified_functions(child, f"{qualname}.<locals>.")
            case ast.ClassDef():
                yield from _qualified_functions(child, f"{prefix}{child.name}.")
            case _:
                yield from _qualified_functions(child, prefix)


def function_source_hashes(source: str) -> Mapping[str, str]:
    """sha256 of each function's source segment, keyed by qualname — the AST
    index the caching layer keys on; unused by the UI itself."""
    return MappingProxyType(
        {
            qualname: hashlib.sha256(segment.encode()).hexdigest()
            for qualname, node in _qualified_functions(ast.parse(source), "")
            if (segment := ast.get_source_segment(source, node)) is not None
        }
    )


def load_model(path: Path) -> LoadedModel | Error:
    try:
        source = path.read_text()
    except OSError as exc:
        return Error(f"cannot read {path}: {exc}")

    namespace: dict = {"__name__": "__fastbuild_model__", "__file__": str(path)}
    try:
        tree = ast.parse(source, filename=str(path))
        code = compile(tree, str(path), "exec")
        exec(code, namespace)
    except Exception:
        return Error(traceback.format_exc())

    apps = [v for v in namespace.values() if isinstance(v, App)]
    if not apps:
        return Error(f"{path} defines no fastbuild App instance")
    app = apps[0]
    if not app.parts:
        return Error(f"{path} registers no @part functions")

    return LoadedModel(
        path=path,
        source=source,
        app=app,
        function_hashes=function_source_hashes(source),
        namespace=namespace,
        parts=app.parts,
    )


def _model_functions(namespace: Mapping[str, Any]) -> Mapping[str, FunctionType]:
    return {
        name: value
        for name, value in namespace.items()
        if isinstance(value, FunctionType) and value.__globals__ is namespace
    }


def install_cache(loaded: LoadedModel, store: Store, *, debug: bool = False) -> LoadedModel:
    """Boundary function, called only by the viewer server: replaces the model
    namespace's functions in place with memoizing wrappers (call-time global
    lookup makes composed calls find them) and returns a LoadedModel whose
    `parts` carry the wrapped entry points — `registry` captured the raw
    functions at decoration time, so namespace rebinding alone never reaches
    `part_def.fn`.
    """
    namespace = loaded.namespace
    model_functions = _model_functions(namespace)
    marked = frozenset(
        name
        for name, fn in model_functions.items()
        if getattr(fn, "_fb_no_cache", False)
    ) | frozenset(
        part.fn.__name__ for part in loaded.parts.values() if not part.cache
    )
    quality = resolve(analyze_functions(loaded.source, loaded.function_hashes), marked)
    wrapped: dict[Any, Any] = {}
    for name, fn in model_functions.items():
        verdict = quality.get(fn.__name__)
        if verdict is None:
            continue
        wrapper = wrapped.get(fn) or memoize(
            fn, qualname=fn.__name__, quality=verdict, store=store, debug=debug
        )
        wrapped[fn] = wrapper
        namespace[name] = wrapper  # type: ignore[index]
    parts = MappingProxyType(
        {
            name: replace(part, fn=wrapped[part.fn]) if part.fn in wrapped else part
            for name, part in loaded.parts.items()
        }
    )
    return replace(loaded, parts=parts)
