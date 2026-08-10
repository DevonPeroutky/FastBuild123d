"""The App registry: @app.part() registration plus signature introspection."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping, TypeVar

from fastbuild.params import ParamError, ParamSpec, introspect

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class PartDef:
    """One registered part: the function, its widget specs, and cache opt-in."""

    name: str
    fn: Callable[..., Any]
    params: tuple[ParamSpec, ...]
    cache: bool = True


class App:
    """Collects @app.part() registrations from one model file.

    The viewer loads the file and serves every registered part; multiple
    parts get a selector in the UI.
    """

    def __init__(self) -> None:
        self._parts: dict[str, PartDef] = {}

    @property
    def parts(self) -> Mapping[str, PartDef]:
        """Registered parts as a read-only mapping keyed by function name."""
        return MappingProxyType(self._parts)

    def part(self, *, cache: bool = True) -> Callable[[F], F]:
        """Register a function as a part; its type-annotated signature defines
        the UI widgets. Every parameter needs a default. Raises ParamError at
        decoration time for signatures the UI cannot represent. `cache=False`
        opts the part's whole call tree out of the geometry cache.
        """

        def register(fn: F) -> F:
            if fn.__name__ in self._parts:
                raise ParamError(f"duplicate @part function name '{fn.__name__}'")
            specs = introspect(fn)
            self._parts[fn.__name__] = PartDef(
                name=fn.__name__, fn=fn, params=specs, cache=cache
            )
            return fn

        return register
