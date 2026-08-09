"""Session state: which parts exist, which is selected, current param values.

Pure — the server holds one SessionState and replaces it via these transition
functions. Values are stored as canonical JSON primitives (the wire format);
conversion to Python values happens only at build time.
"""

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Mapping

from fastbuild.errors import Error
from fastbuild.params import JsonValue, coerce, default_value, schema
from fastbuild.protocol import Init, PartSchema
from fastbuild.registry import PartDef

Values = Mapping[str, JsonValue]


@dataclass(frozen=True)
class SessionState:
    parts: Mapping[str, PartDef]
    selected: str
    values: Mapping[str, Values]


def _frozen(values: dict[str, dict[str, JsonValue]]) -> Mapping[str, Values]:
    return MappingProxyType({k: MappingProxyType(dict(v)) for k, v in values.items()})


def initial_state(parts: Mapping[str, PartDef]) -> SessionState:
    values = {
        name: {spec.name: default_value(spec) for spec in part.params}
        for name, part in parts.items()
    }
    return SessionState(
        parts=parts,
        selected=next(iter(parts)),
        values=_frozen(values),
    )


def set_value(state: SessionState, part: str, name: str, raw: object) -> SessionState | Error:
    part_def = state.parts.get(part)
    if part_def is None:
        return Error(f"unknown part '{part}'")
    specs = {spec.name: spec for spec in part_def.params}
    if name not in specs:
        return Error(f"part '{part}' has no parameter '{name}'")
    value = coerce(specs[name], raw)
    if isinstance(value, Error):
        return value
    updated = {k: dict(v) for k, v in state.values.items()}
    updated[part][name] = value
    return replace(state, values=_frozen(updated))


def select_part(state: SessionState, part: str) -> SessionState | Error:
    if part not in state.parts:
        return Error(f"unknown part '{part}'")
    return replace(state, selected=part)


def migrate(state: SessionState, new_parts: Mapping[str, PartDef]) -> SessionState:
    """After a file save: params that survive keep their current values (when
    they still coerce), new params start at defaults, removed ones drop."""
    fresh = initial_state(new_parts)
    values = {}
    for name, part in fresh.parts.items():
        merged = dict(fresh.values[name])
        old = state.values.get(name, {})
        for spec in part.params:
            if spec.name in old:
                value = coerce(spec, old[spec.name])
                if not isinstance(value, Error):
                    merged[spec.name] = value
        values[name] = merged
    selected = state.selected if state.selected in fresh.parts else fresh.selected
    return SessionState(parts=fresh.parts, selected=selected, values=_frozen(values))


def init_message(state: SessionState) -> Init:
    return Init(
        parts=tuple(
            PartSchema(
                name=name,
                params=tuple(
                    schema(spec, state.values[name][spec.name]) for spec in part.params
                ),
            )
            for name, part in state.parts.items()
        ),
        selected=state.selected,
    )
