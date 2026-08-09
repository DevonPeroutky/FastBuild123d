"""AST call-closure analysis: what source a function's key must cover, and
whether it is safe to cache at all. Pure — findings come back as data.

Cacheability is a property of the transitive closure, not the node: a cached
caller's hit short-circuits every callee, so impurity, unresolvable calls and
`@no_cache` are all contagious upward over the same closure the source hashes
traverse (SPEC-caching §3 Analysis).
"""

import ast
import builtins
import hashlib
from dataclasses import dataclass
from typing import Mapping

IMPURE_NAMES = frozenset(
    {"open", "import_svg", "import_step", "environ", "random", "time", "input"}
)

_BUILTIN_NAMES = frozenset(dir(builtins))


@dataclass(frozen=True)
class FunctionNode:
    name: str
    source_hash: str
    callees: frozenset[str]
    impure: tuple[str, ...]
    unresolved: tuple[str, ...]
    no_cache: bool


@dataclass(frozen=True)
class Cacheability:
    closure_hash: str
    reasons: tuple[str, ...]
    members: tuple[str, ...]

    @property
    def cacheable(self) -> bool:
        return not self.reasons


def _assigned_names(node: ast.stmt) -> tuple[str, ...]:
    match node:
        case ast.Assign(targets=targets):
            return tuple(
                target.id for target in targets if isinstance(target, ast.Name)
            )
        case ast.AnnAssign(target=ast.Name(id=name)) | ast.AugAssign(
            target=ast.Name(id=name)
        ):
            return (name,)
        case _:
            return ()


def _imported_names(node: ast.stmt) -> tuple[str, ...]:
    match node:
        case ast.Import(names=names):
            return tuple(a.asname or a.name.split(".")[0] for a in names)
        case ast.ImportFrom(names=names):
            return tuple(a.asname or a.name for a in names)
        case _:
            return ()


def _has_no_cache_decorator(fn: ast.FunctionDef) -> bool:
    return any(
        isinstance(d, ast.Name)
        and d.id == "no_cache"
        or isinstance(d, ast.Attribute)
        and d.attr == "no_cache"
        for d in fn.decorator_list
    )


def _classify_calls(
    fn: ast.FunctionDef, functions: frozenset[str], external: frozenset[str]
) -> tuple[frozenset[str], tuple[str, ...]]:
    callees: set[str] = set()
    unresolved: list[str] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        match node.func:
            case ast.Name(id=name) if name in functions:
                callees.add(name)
            case ast.Name(id=name) if name in external or name in _BUILTIN_NAMES:
                pass
            case ast.Name(id=name):
                unresolved.append(f"call to '{name}' cannot be resolved")
            case ast.Attribute():
                pass
            case _:
                unresolved.append("dynamic call target")
    return frozenset(callees), tuple(unresolved)


def analyze_functions(
    source: str, source_hashes: Mapping[str, str]
) -> Mapping[str, FunctionNode]:
    """One node per top-level function, with its resolved model-file callees."""
    tree = ast.parse(source)
    defs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    functions = frozenset(d.name for d in defs)
    external = frozenset(
        name
        for stmt in tree.body
        for name in (*_imported_names(stmt), *_assigned_names(stmt))
    ) - functions
    nodes = {}
    for fn in defs:
        callees, unresolved = _classify_calls(fn, functions, external)
        impure = tuple(
            sorted(
                {
                    node.id
                    for node in ast.walk(fn)
                    if isinstance(node, ast.Name) and node.id in IMPURE_NAMES
                }
            )
        )
        nodes[fn.name] = FunctionNode(
            name=fn.name,
            source_hash=source_hashes.get(fn.name, ""),
            callees=callees,
            impure=impure,
            unresolved=unresolved,
            no_cache=_has_no_cache_decorator(fn),
        )
    return nodes


def _closure(nodes: Mapping[str, FunctionNode], root: str) -> tuple[str, ...]:
    seen = {root}
    stack = [root]
    while stack:
        for callee in nodes[stack.pop()].callees:
            if callee not in seen:
                seen.add(callee)
                stack.append(callee)
    return tuple(sorted(seen))


def resolve(
    nodes: Mapping[str, FunctionNode],
    extra_no_cache: frozenset[str] = frozenset(),
) -> Mapping[str, Cacheability]:
    """Fold each function's transitive closure into its hash and its verdict."""
    out = {}
    for name in nodes:
        members = _closure(nodes, name)
        digest = hashlib.sha256(
            "\n".join(f"{m}:{nodes[m].source_hash}" for m in members).encode()
        ).hexdigest()
        reasons = []
        for member in members:
            node = nodes[member]
            via = "" if member == name else f" (via '{member}')"
            reasons.extend(f"impure name '{n}'{via}" for n in node.impure)
            reasons.extend(f"{u}{via}" for u in node.unresolved)
            if node.no_cache or member in extra_no_cache:
                reasons.append(f"@no_cache{via}")
        out[name] = Cacheability(
            closure_hash=digest, reasons=tuple(reasons), members=members
        )
    return out
