"""Snapshot local Python sources and import helpers from that same revision."""

import builtins
import hashlib
import importlib
import os
import sys
from dataclasses import dataclass
from importlib.abc import MetaPathFinder
from importlib.machinery import PathFinder, SourceFileLoader
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


_EXCLUDED = frozenset({"__pycache__", "venv", "env", "node_modules", "site-packages"})


def source_root(model_path: Path) -> Path:
    root = model_path.resolve().parent
    while (root / "__init__.py").is_file() and (root.parent / "__init__.py").is_file():
        root = root.parent
    return root


def is_model_source(root: Path, path: Path) -> bool:
    try:
        relative = path.absolute().relative_to(root)
    except ValueError:
        return False
    return path.suffix == ".py" and all(
        not part.startswith(".") and part not in _EXCLUDED
        for part in relative.parts[:-1]
    )


@dataclass(frozen=True)
class Sources:
    root: Path
    files: Mapping[Path, bytes]
    digest: str


def read_sources(model_path: Path) -> Sources:
    root = source_root(model_path)
    files = {}
    for directory, subdirs, names in os.walk(root, onerror=_raise_walk_error):
        subdirs[:] = sorted(
            name for name in subdirs
            if not name.startswith(".") and name not in _EXCLUDED
        )
        for name in sorted(names):
            path = Path(directory) / name
            if path.suffix == ".py":
                files[path] = path.read_bytes()
    digest = hashlib.sha256()
    digest.update(str(model_path.resolve()).encode())
    for path, content in sorted(files.items()):
        digest.update(b"\0" + str(path.relative_to(root)).encode() + b"\0")
        digest.update(hashlib.sha256(content).digest())
    return Sources(root, MappingProxyType(files), digest.hexdigest())


def _raise_walk_error(error: OSError) -> None:
    raise error


class _SnapshotLoader(SourceFileLoader):
    def __init__(self, name: str, path: str, content: bytes, importer: "ModelImports"):
        super().__init__(name, path)
        self.content = content
        self.importer = importer

    def get_code(self, fullname):
        return compile(self.content, self.path, "exec")

    def exec_module(self, module):
        module.__dict__["__builtins__"] = self.importer.builtins
        super().exec_module(module)


class ModelImports(MetaPathFinder):
    def __init__(self, sources: Sources):
        self.sources = sources
        self.builtins = {**vars(builtins), "__import__": self.import_module}

    def find_spec(self, fullname, path=None, target=None):
        spec = PathFinder.find_spec(fullname, path, target)
        if spec is None or spec.origin is None:
            return None
        content = self.sources.files.get(Path(spec.origin).absolute())
        if content is None:
            if is_model_source(self.sources.root, Path(spec.origin)):
                raise ImportError(f"{spec.origin} is not in the loaded source snapshot")
            return None
        spec.loader = _SnapshotLoader(fullname, spec.origin, content, self)
        return spec

    def import_module(self, name, globals=None, locals=None, fromlist=(), level=0):
        root = self.sources.root
        search_path = root.parent if (root / "__init__.py") in self.sources.files else root
        sys.meta_path.insert(0, self)
        sys.path.insert(0, str(search_path))
        try:
            return builtins.__import__(name, globals, locals, fromlist, level)
        finally:
            sys.path.pop(0)
            sys.meta_path.remove(self)

    def evict_modules(self):
        previous = {
            name: module for name, module in tuple(sys.modules.items())
            if name != "fastbuild" and not name.startswith("fastbuild.")
            and (
                (path := getattr(module, "__file__", None))
                and is_model_source(self.sources.root, Path(path))
                or any(
                    is_model_source(self.sources.root, Path(directory) / "__init__.py")
                    for directory in getattr(module, "__path__", ())
                )
            )
        }
        for name in previous:
            del sys.modules[name]
        importlib.invalidate_caches()
        return previous
