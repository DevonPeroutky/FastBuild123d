"""Load a model file: source → AST → compile → exec into a fresh namespace.

The harness (not `import`) owns the exact source text, which is what the
caching layer keys on (SPEC-caching §3.1). Load failures are returned, not
raised — a broken file mid-edit is an expected condition.
"""

import ast
import hashlib
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterator, Mapping

from fastbuild.errors import Error
from fastbuild.registry import App


@dataclass(frozen=True)
class LoadedModel:
    path: Path
    source: str
    app: App
    function_hashes: Mapping[str, str]


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
    )
