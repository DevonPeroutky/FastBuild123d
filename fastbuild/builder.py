"""Build execution: part function → geometry → viewer shape JSON.

Each shape node in the output carries its tree path as `id` — the slot the
caching layer's provenance keys plug into (SPEC-ui §6).
"""

import traceback
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Mapping, Union

from ocp_tessellate.convert import numpy_to_buffer_json, tessellate_group, to_ocpgroup

from fastbuild.cache.keys import CacheKey
from fastbuild.cache.store import Store
from fastbuild.params import to_python
from fastbuild.registry import PartDef


@dataclass(frozen=True)
class BuildOutput:
    part: str
    shapes: Mapping[str, Any]
    timings: Mapping[str, float]


@dataclass(frozen=True)
class BuildFailure:
    part: str
    traceback: str


BuildResult = Union[BuildOutput, BuildFailure]


def tessellate_to_json(obj: Any, name: str) -> Mapping[str, Any]:
    group, instances = to_ocpgroup(obj, names=[name])
    instance_meshes, shapes, _id_map = tessellate_group(group, instances)
    return numpy_to_buffer_json({"instances": instance_meshes, "shapes": shapes})


def _mesh_key(part_def: PartDef, call_args: Mapping[str, Any]) -> CacheKey | None:
    """The part function's *call* key, computable before the build runs — a
    revisit that still paid the build could never land inside the §2.2 budget.
    None when the entry point is unwrapped (no store installed) or unkeyable.
    """
    key_fn = getattr(part_def.fn, "call_key", None)
    if key_fn is None:
        return None
    key = key_fn(call_args)
    return key if isinstance(key, CacheKey) else None


def build_part(
    part_def: PartDef, values: Mapping[str, Any], store: Store | None = None
) -> BuildResult:
    """Build the part and return the result. `store` is the viewer server's
    cache; None (CLI, tests, bench) means no mesh caching."""
    call_args = {
        spec.name: to_python(spec, values[spec.name])
        for spec in part_def.params
        if spec.name in values
    }
    key = _mesh_key(part_def, call_args) if store is not None else None
    if store is not None and key is not None:
        mesh = store.get_mesh(key)
        if mesh is not None:
            return BuildOutput(
                part=part_def.name,
                shapes=mesh,
                timings={"build_ms": 0.0, "tessellate_ms": 0.0},
            )
    start = perf_counter()
    try:
        result = part_def.fn(**call_args)
    except Exception:
        return BuildFailure(part=part_def.name, traceback=traceback.format_exc())
    built = perf_counter()
    try:
        shapes = tessellate_to_json(result, part_def.name)
    except Exception:
        return BuildFailure(part=part_def.name, traceback=traceback.format_exc())
    done = perf_counter()
    if store is not None and key is not None:
        store.put_mesh(key, shapes)
    return BuildOutput(
        part=part_def.name,
        shapes=shapes,
        timings={
            "build_ms": round((built - start) * 1000, 1),
            "tessellate_ms": round((done - built) * 1000, 1),
        },
    )
