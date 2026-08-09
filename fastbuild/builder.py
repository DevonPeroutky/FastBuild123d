"""Build execution: part function → geometry → viewer shape JSON.

Each shape node in the output carries its tree path as `id` — the slot the
caching layer's provenance keys plug into (SPEC-ui §6).

Meshes are cached by handing `ocp_tessellate` our provenance digest as its
per-instance cache id instead of letting it hash the shape's serialized bytes
(SPEC-mesh-caching §3). Its own content hash cannot see through the store's
`BRepBuilderAPI_Copy`, so a cached child would otherwise re-mesh at full price.
"""

import traceback
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Mapping, Union

from build123d import Compound
from ocp_tessellate.cad_objects import OcpGroup, OcpObject
from ocp_tessellate.convert import numpy_to_buffer_json, tessellate_group, to_ocpgroup

from fastbuild.cache.keys import TAG_ATTR, CacheKey
from fastbuild.params import to_python
from fastbuild.registry import PartDef

FB_CACHE_PREFIX = "fb:"


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


def _provenance_ids(root: Any, group: OcpGroup) -> tuple[tuple[int, str], ...]:
    """(instance ref, cache id) pairs, but only when `group` is a flat,
    name-matching image of `root.children`. Any other structure yields ()
    and leaves ocp_tessellate's own content hashing in place — the mapping is
    either exact or absent, so a mesh can never be filed under a sibling's key.
    """
    children = tuple(root.children) if isinstance(root, Compound) and root.children else ()
    objects = group.objects
    if not children or len(objects) != len(children):
        return ()
    ids = []
    for obj, child in zip(objects, children):
        if not isinstance(obj, OcpObject) or obj.ref is None or obj.name != child.label:
            return ()
        key = getattr(child, TAG_ATTR, None)
        if isinstance(key, CacheKey):
            ids.append((obj.ref, FB_CACHE_PREFIX + key.digest))
    return tuple(ids)


def tessellate_to_json(obj: Any, name: str) -> Mapping[str, Any]:
    group, instances = to_ocpgroup(obj, names=[name])
    for ref, cache_id in _provenance_ids(obj, group):
        instances[ref]["cache_id"] = cache_id
    instance_meshes, shapes, _id_map = tessellate_group(group, instances)
    return numpy_to_buffer_json({"instances": instance_meshes, "shapes": shapes})


def build_part(part_def: PartDef, values: Mapping[str, Any]) -> BuildResult:
    """Build the part and return the result. Caching is engaged iff
    `harness.install_cache` wrapped the model's functions."""
    call_args = {
        spec.name: to_python(spec, values[spec.name])
        for spec in part_def.params
        if spec.name in values
    }
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
    return BuildOutput(
        part=part_def.name,
        shapes=shapes,
        timings={
            "build_ms": round((built - start) * 1000, 1),
            "tessellate_ms": round((done - built) * 1000, 1),
        },
    )
