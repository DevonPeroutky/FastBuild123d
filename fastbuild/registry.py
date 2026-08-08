"""The App registry: @app.part() registration plus signature introspection."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from fastbuild.params import ParamError, ParamSpec, introspect


@dataclass(frozen=True)
class PartDef:
    name: str
    fn: Callable[..., Any]
    params: tuple[ParamSpec, ...]
    version: int = 0
    cache: bool = True


class App:
    """Collects @app.part() registrations from one model file.

    `version` and `cache` are accepted now so model files written against the
    caching layer (SPEC-caching) keep working; the UI layer ignores them.
    """

    def __init__(self) -> None:
        self._parts: dict[str, PartDef] = {}

    @property
    def parts(self) -> Mapping[str, PartDef]:
        return MappingProxyType(self._parts)

    def part(self, *, version: int = 0, cache: bool = True) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def register(fn: Callable[..., Any]) -> Callable[..., Any]:
            if fn.__name__ in self._parts:
                raise ParamError(f"duplicate @part function name '{fn.__name__}'")
            specs = introspect(fn)
            self._parts[fn.__name__] = PartDef(
                name=fn.__name__, fn=fn, params=specs, version=version, cache=cache
            )
            return fn

        return register
