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
from typing import Mapping

from fastbuild.errors import Error
from fastbuild.registry import App


@dataclass(frozen=True)
class LoadedModel:
    path: Path
    source: str
    app: App
    function_hashes: Mapping[str, str]


def function_source_hashes(source: str) -> Mapping[str, str]:
    """sha256 of each top-level function's source segment — the AST index the
    caching layer keys on; unused by the UI itself."""
    tree = ast.parse(source)
    hashes = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            segment = ast.get_source_segment(source, node)
            if segment is not None:
                hashes[node.name] = hashlib.sha256(segment.encode()).hexdigest()
    return MappingProxyType(hashes)


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
