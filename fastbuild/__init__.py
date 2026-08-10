from importlib.metadata import version

from fastbuild.cache.wrap import no_cache
from fastbuild.params import Group, ParamError, Range
from fastbuild.registry import App

__version__ = version("fastbuild123")

__all__ = ["App", "Group", "ParamError", "Range", "no_cache", "__version__"]
