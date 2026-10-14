"""Describe supported local functions and calls before installing wrappers."""

import ast
import builtins
from dataclasses import dataclass
from pathlib import Path
from types import FunctionType, MappingProxyType, ModuleType
from typing import Any, Mapping

from fastbuild.cache.analysis import FunctionNode, IMPURE_NAMES, _BUILTIN_NAMES


@dataclass(frozen=True)
class LocalFunction:
    identity: str
    fn: FunctionType
    node: ast.FunctionDef


def function_identity(path: Path, qualname: str) -> str:
    return f"{path.as_posix()}::{qualname}"


def local_functions(sources: Mapping[Path, bytes], namespaces: Mapping[Path, Mapping[str, Any]]) -> tuple[LocalFunction, ...]:
    result = []
    for path, namespace in namespaces.items():
        for node in ast.parse(sources[path]).body:
            if not isinstance(node, ast.FunctionDef):
                continue
            fn = namespace.get(node.name)
            if (isinstance(fn, FunctionType) and fn.__globals__ is namespace
                    and fn.__qualname__ == node.name
                    and fn.__code__.co_name == node.name
                    and fn.__code__.co_filename == str(path)):
                result.append(LocalFunction(function_identity(path, fn.__qualname__), fn, node))
    return tuple(result)


def binding(node: ast.expr, namespace: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Name):
        return namespace.get(node.id)
    if isinstance(node, ast.Attribute):
        parent = binding(node.value, namespace)
        if isinstance(parent, ModuleType):
            return vars(parent).get(node.attr)
    return None


def analyze_local(functions: tuple[LocalFunction, ...]) -> Mapping[str, FunctionNode]:
    identities = {item.fn: item.identity for item in functions}
    nodes = {}
    for item in functions:
        callees, impure, unresolved = set(), set(), set()
        namespace = item.fn.__globals__
        local_names = set(item.fn.__code__.co_varnames) | set(item.fn.__code__.co_cellvars)
        for node in ast.walk(item.node):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                unresolved.add('runtime import')
            if isinstance(node, ast.Name) and node.id in IMPURE_NAMES:
                impure.add(node.id)
            if not isinstance(node, ast.Call):
                continue
            target = binding(node.func, namespace)
            root = node.func
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in local_names:
                target = None
                if isinstance(node.func, ast.Name) or root.id in namespace:
                    unresolved.add(f"call through local binding '{root.id}' cannot be resolved")
            if isinstance(target, FunctionType) and target in identities:
                callees.add(identities[target])
            elif isinstance(node.func, ast.Name):
                if node.func.id not in namespace and node.func.id not in _BUILTIN_NAMES:
                    unresolved.add(f"call to '{node.func.id}' cannot be resolved")
            elif not isinstance(node.func, ast.Attribute):
                unresolved.add('dynamic call target')
            if (isinstance(node.func, ast.Name) and node.func.id in {'__import__', 'eval', 'exec'}
                    or isinstance(node.func, ast.Attribute) and node.func.attr in {'import_module', '__import__', 'exec_module', 'load_module'}
                    or target is builtins.__import__
                    or isinstance(target, FunctionType) and target.__module__ == 'importlib' and target.__name__ == 'import_module'):
                unresolved.add('runtime import')
        nodes[item.identity] = FunctionNode(
            item.identity, '', frozenset(callees), tuple(sorted(impure)),
            tuple(sorted(unresolved)), bool(vars(item.fn).get('_fb_no_cache', False)),
        )
    return MappingProxyType(nodes)
