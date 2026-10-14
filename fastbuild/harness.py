"""Load a model file: source → AST → compile → exec into a fresh namespace.

The harness (not `import`) owns the exact source text, which is what the
caching layer keys on (SPEC-caching §3.1). Load failures are returned, not
raised — a broken file mid-edit is an expected condition.
"""

import ast
import hashlib
import sys
import traceback
from dataclasses import dataclass, replace
from pathlib import Path
from types import FunctionType, MappingProxyType
from typing import Any, Iterator, Mapping

from fastbuild.cache.analysis import resolve
from fastbuild.cache.graph import local_functions, analyze_local
from fastbuild.cache.runtime import CacheRuntime
from fastbuild.cache.store import Store
from fastbuild.cache.wrap import memoize
from fastbuild.errors import Error
from fastbuild.registry import App, PartDef
from fastbuild.sources import ModelImports, Sources, read_sources


@dataclass(frozen=True)
class LoadedModel:
    path: Path
    source: str
    app: App
    function_hashes: Mapping[str, str]
    namespace: Mapping[str, Any]
    parts: Mapping[str, PartDef]
    sources: Sources
    namespaces: Mapping[Path, Mapping[str, Any]]


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
    path = path.resolve()
    try:
        sources = read_sources(path)
        source = sources.files[path].decode("utf-8")
    except KeyError:
        return Error(f"cannot read {path}: file is missing from model sources")
    except (OSError, UnicodeError) as exc:
        return Error(f"cannot read {path}: {exc}")

    imports = ModelImports(sources)
    package_root = (
        sources.root.parent
        if (sources.root / "__init__.py") in sources.files else sources.root
    )
    package = ".".join(path.parent.relative_to(package_root).parts)
    namespace: dict = {
        "__name__": "__fastbuild_model__", "__file__": str(path),
        "__package__": package, "__builtins__": imports.builtins,
    }
    previous_modules = imports.evict_modules()
    succeeded = False
    try:
        tree = ast.parse(source, filename=str(path))
        code = compile(tree, str(path), "exec")
        exec(code, namespace)

        apps = [v for v in namespace.values() if isinstance(v, App)]
        if not apps:
            return Error(f"{path} defines no fastbuild App instance")
        app = apps[0]
        if not app.parts:
            return Error(f"{path} registers no @part functions")
        loaded = LoadedModel(
            path=path,
            source=source,
            app=app,
            function_hashes=function_source_hashes(source),
            namespace=namespace,
            parts=app.parts,
            sources=sources,
            namespaces=MappingProxyType({path: namespace, **{p: vars(m) for p, m in imports.modules.items()}}),
        )
        succeeded = True
        return loaded
    except Exception:
        return Error(traceback.format_exc())
    finally:
        if not succeeded:
            imports.evict_modules()
            sys.modules.update(previous_modules)


def install_cache(
    loaded: LoadedModel, store: Store, *, debug: bool = False,
    runtime: CacheRuntime | None = None,
) -> LoadedModel:
    """Install one wrapper per function and rebind supported local aliases."""
    runtime = runtime if runtime is not None else CacheRuntime()
    functions = local_functions(loaded.sources.files, loaded.namespaces)
    nodes = analyze_local(functions)
    disabled = {part.fn for part in loaded.parts.values() if not part.cache}
    marked = frozenset(item.identity for item in functions if item.fn in disabled)
    quality = resolve(nodes, marked)
    wrapped = {
        item.fn: memoize(
            item.fn, qualname=item.identity,
            quality=replace(quality[item.identity], closure_hash=loaded.sources.digest),
            store=store, runtime=runtime, debug=debug,
            disable_tree=item.fn in disabled or any(
                'runtime import' in reason for reason in quality[item.identity].reasons
            ),
        )
        for item in functions
    }
    for namespace in loaded.namespaces.values():
        for name, value in tuple(namespace.items()):
            if isinstance(value, FunctionType) and value in wrapped:
                namespace[name] = wrapped[value]
    parts = MappingProxyType({
        name: replace(part, fn=wrapped[part.fn]) if part.fn in wrapped else part
        for name, part in loaded.parts.items()
    })
    return replace(loaded, parts=parts)
