"""Parameter model: Range annotations, widget specs, introspection, coercion.

Everything here is pure. A function signature goes in, a tuple of ParamSpec
variants comes out; raw JSON values go in, coerced/quantized values come out.

Type checking of incoming values is delegated to pydantic in strict mode (one
memoized TypeAdapter per spec); the clamping, step-snapping and error wording
around it stay hand-written, because pydantic's numeric constraints reject
out-of-range values rather than clamping them onto the slider grid.
"""

import enum
import inspect
import typing
from dataclasses import dataclass
from functools import lru_cache, partial
from typing import Annotated, Any, Callable, Literal, Sequence, Union

import annotated_types as at
from pydantic import AfterValidator, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo

from fastbuild.errors import Error

JsonValue = Union[int, float, str, bool]


@dataclass(frozen=True)
class Range:
    min: float
    max: float
    step: float | None = None
    unit: str | None = None


class ParamError(Exception):
    """Raised at decoration time for signatures the UI cannot represent."""


@dataclass(frozen=True)
class Slider:
    name: str
    min: float
    max: float
    step: float
    integer: bool
    default: JsonValue
    unit: str | None = None


@dataclass(frozen=True)
class NumberInput:
    name: str
    integer: bool
    default: JsonValue
    unit: str | None = None


@dataclass(frozen=True)
class Checkbox:
    name: str
    default: bool


@dataclass(frozen=True)
class Dropdown:
    name: str
    options: tuple[JsonValue, ...]
    default: JsonValue
    enum_class: type[enum.Enum] | None = None


@dataclass(frozen=True)
class Unsupported:
    name: str
    default_repr: str


ParamSpec = Union[Slider, NumberInput, Checkbox, Dropdown, Unsupported]


def _range_from_metadata(name: str, metadata: Sequence[Any]) -> Range | None:
    """Read a Range out of `annotated_types` / pydantic `Field` metadata, so
    `Annotated[float, Field(ge=0, le=10)]` builds a slider just like `Range` does.

    A bare `multiple_of` with no bounds is not enough to place a slider, so it
    falls through to a plain number input and the step is ignored.
    """
    bounds: dict[str, float] = {}
    unit: str | None = None
    pending = list(metadata)
    while pending:
        item = pending.pop(0)
        if isinstance(item, FieldInfo):
            pending.extend(item.metadata)
            extra = item.json_schema_extra
            if isinstance(extra, dict) and isinstance(extra.get("unit"), str):
                unit = extra["unit"]
        elif isinstance(item, at.Interval):
            pending.extend(item)
        elif isinstance(item, at.Ge):
            bounds["min"] = item.ge
        elif isinstance(item, at.Le):
            bounds["max"] = item.le
        elif isinstance(item, at.MultipleOf):
            bounds["step"] = item.multiple_of
        elif isinstance(item, (at.Gt, at.Lt)):
            raise ParamError(
                f"parameter '{name}': exclusive bounds (gt/lt) cannot define a "
                f"slider range; use ge/le or Range(min, max)"
            )

    if "min" not in bounds and "max" not in bounds:
        return None
    if "min" not in bounds or "max" not in bounds:
        raise ParamError(
            f"parameter '{name}': a slider needs both a lower (ge) and an upper "
            f"(le) bound"
        )
    return Range(
        min=bounds["min"], max=bounds["max"], step=bounds.get("step"), unit=unit
    )


def _unwrap_annotated(name: str, hint: Any) -> tuple[Any, Range | None]:
    if typing.get_origin(hint) is not Annotated:
        return hint, None
    base, *metadata = typing.get_args(hint)
    native = [m for m in metadata if isinstance(m, Range)]
    if native:
        return base, native[0]
    return base, _range_from_metadata(name, metadata)


def _default_step(base: type, rng: Range) -> float:
    if base is int:
        return 1
    return (rng.max - rng.min) / 100


def _spec_for(name: str, hint: Any, default: Any) -> ParamSpec:
    base, rng = _unwrap_annotated(name, hint)

    if base is bool:
        if not isinstance(default, bool):
            raise ParamError(f"parameter '{name}': default {default!r} is not a bool")
        return Checkbox(name=name, default=default)

    if typing.get_origin(base) is Literal:
        options = typing.get_args(base)
        if default not in options:
            raise ParamError(f"parameter '{name}': default {default!r} not in {options}")
        return Dropdown(name=name, options=tuple(options), default=default)

    if isinstance(base, type) and issubclass(base, enum.Enum):
        if not isinstance(default, base):
            raise ParamError(f"parameter '{name}': default {default!r} is not a {base.__name__}")
        return Dropdown(
            name=name,
            options=tuple(m.name for m in base),
            default=default.name,
            enum_class=base,
        )

    if base in (int, float):
        if not isinstance(default, (int, float)) or isinstance(default, bool):
            raise ParamError(f"parameter '{name}': default {default!r} is not a number")
        integer = base is int
        if rng is None:
            return NumberInput(name=name, integer=integer, default=default)
        if not rng.min < rng.max:
            raise ParamError(f"parameter '{name}': Range min {rng.min} must be < max {rng.max}")
        step = rng.step if rng.step is not None else _default_step(base, rng)
        if step <= 0:
            raise ParamError(f"parameter '{name}': Range step {step} must be > 0")
        return Slider(
            name=name,
            min=rng.min,
            max=rng.max,
            step=step,
            integer=integer,
            default=default,
            unit=rng.unit,
        )

    return Unsupported(name=name, default_repr=repr(default))


def introspect(fn: Callable[..., Any]) -> tuple[ParamSpec, ...]:
    """Derive the widget spec for each parameter of a @part function."""
    signature = inspect.signature(fn)
    hints = typing.get_type_hints(fn, include_extras=True)
    specs = []
    for name, param in signature.parameters.items():
        if param.default is inspect.Parameter.empty:
            raise ParamError(
                f"parameter '{name}' of '{fn.__name__}' has no default value; "
                f"every @part parameter needs one"
            )
        hint = hints.get(name, type(param.default))
        specs.append(_spec_for(name, hint, param.default))
    return tuple(specs)


def quantize(spec: Slider, value: float) -> JsonValue:
    """Clamp to [min, max] and snap onto the step grid, so equal slider
    positions always produce bit-identical values (stable cache keys)."""
    clamped = min(max(value, spec.min), spec.max)
    steps = round((clamped - spec.min) / spec.step)
    snapped = min(spec.min + steps * spec.step, spec.max)
    if spec.integer:
        return int(round(snapped))
    return round(snapped, 12)


@lru_cache(maxsize=256)
def _coerce_adapter(spec: ParamSpec) -> TypeAdapter[Any] | None:
    """The type gate for a live parameter edit. None means the spec takes no input.

    Strict mode is what rejects `True` for a number and `1` for a checkbox;
    everything past the gate is this module's own canonicalisation.
    """
    match spec:
        case Slider():
            return TypeAdapter(Annotated[float, AfterValidator(partial(quantize, spec))])
        case NumberInput():
            return TypeAdapter(Annotated[float, AfterValidator(int if spec.integer else float)])
        case Checkbox():
            return TypeAdapter(bool)
        case Dropdown():
            return TypeAdapter(Literal[spec.options])
        case Unsupported():
            return None


def _rejection(spec: ParamSpec, raw: Any) -> str:
    match spec:
        case Checkbox():
            return f"'{spec.name}' expects a bool, got {raw!r}"
        case Dropdown():
            return f"'{spec.name}' expects one of {spec.options}, got {raw!r}"
        case _:
            return f"'{spec.name}' expects a number, got {raw!r}"


def coerce(spec: ParamSpec, raw: Any) -> JsonValue | Error:
    """Validate a raw JSON value against a spec, returning the canonical value."""
    adapter = _coerce_adapter(spec)
    if adapter is None:
        return Error(f"'{spec.name}' is read-only (unsupported parameter type)")
    try:
        return adapter.validate_python(raw, strict=True)
    except ValidationError:
        return Error(_rejection(spec, raw))


def default_value(spec: ParamSpec) -> JsonValue:
    match spec:
        case Slider() | NumberInput() | Checkbox() | Dropdown():
            return spec.default
        case Unsupported():
            return spec.default_repr


def to_python(spec: ParamSpec, value: JsonValue) -> Any:
    """Convert a canonical JSON value into the value the part function is called with."""
    match spec:
        case Dropdown(enum_class=cls) if cls is not None:
            return cls[str(value)]
        case Slider() | NumberInput() | Checkbox() | Dropdown() | Unsupported():
            return value


def schema(spec: ParamSpec, value: JsonValue) -> dict[str, Any]:
    """The wire representation of one parameter, per the init message."""
    base: dict[str, Any] = {
        "name": spec.name,
        "kind": None,
        "min": None,
        "max": None,
        "step": None,
        "unit": None,
        "options": None,
        "default": None,
        "value": value,
    }
    match spec:
        case Slider():
            return {
                **base,
                "kind": "slider",
                "min": spec.min,
                "max": spec.max,
                "step": spec.step,
                "unit": spec.unit,
                "default": spec.default,
            }
        case NumberInput():
            return {**base, "kind": "number", "unit": spec.unit, "default": spec.default}
        case Checkbox():
            return {**base, "kind": "checkbox", "default": spec.default}
        case Dropdown():
            return {**base, "kind": "dropdown", "options": list(spec.options), "default": spec.default}
        case Unsupported():
            return {**base, "kind": "readonly", "default": spec.default_repr}


_OUT_OF_RANGE = ("greater_than_equal", "less_than_equal")


@lru_cache(maxsize=256)
def _bounds_adapter(spec: ParamSpec) -> TypeAdapter[Any] | None:
    """Like `_coerce_adapter`, but sliders reject out-of-range input instead of
    clamping it. None means the spec has no bounds and `coerce` applies as-is."""
    match spec:
        case Slider():
            return TypeAdapter(
                Annotated[
                    float,
                    at.Ge(spec.min),
                    at.Le(spec.max),
                    AfterValidator(int if spec.integer else float),
                ]
            )
        case _:
            return None


def validate_bounds(spec: ParamSpec, raw: Any) -> JsonValue | Error:
    """Strict validation for the export CLI: out-of-range is an error, not a clamp."""
    adapter = _bounds_adapter(spec)
    if adapter is None:
        return coerce(spec, raw)
    try:
        return adapter.validate_python(raw, strict=True)
    except ValidationError as exc:
        if exc.errors()[0]["type"] in _OUT_OF_RANGE:
            return Error(f"'{spec.name}'={raw} is outside Range({spec.min}, {spec.max})")
        return Error(_rejection(spec, raw))
