"""Cache subsystem tests (SPEC-caching §5, SPEC-mesh-caching §5): key laws,
tagging laws, wiring, staleness, poisoning, guards, contagion, provenance-keyed
meshing, and server behavior."""

import asyncio
import copy
import json
import logging
import pickle
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter

import pytest
from build123d import Box
from ocp_tessellate.convert import to_ocpgroup
from ocp_tessellate.tessellator import cache as mesh_cache

from fastbuild.builder import (
    FB_CACHE_PREFIX,
    BuildOutput,
    _provenance_ids,
    build_part,
)
from fastbuild.cache.analysis import analyze_functions, resolve
from fastbuild.cache.keys import CacheKey, encode_arg, fingerprint
from fastbuild.cache.store import Store
from fastbuild.errors import Error
from fastbuild.harness import (
    LoadedModel,
    function_source_hashes,
    install_cache,
    load_model,
)
from fastbuild.cache.store import FrozenStats
from fastbuild.params import default_value
from fastbuild.protocol import BuildOk, encode
from fastbuild.server import ViewerServer, cache_summary

CACHE_MODEL = textwrap.dedent(
    """
    from typing import Annotated
    from build123d import Box, Compound, Pos
    from fastbuild import App, Range

    app = App()

    def base(width: float):
        return Box(width, 8, 4)

    def stud_height(i: int) -> float:
        return 2.0 + (i % 2)

    def studded(body, count: int):
        studs = [Pos(6 + 3.0 * i, 0, 4) * Box(1, 1, stud_height(i)) for i in range(count)]
        return Compound(children=[body, *studs])

    @app.part()
    def widget(
        width: Annotated[float, Range(10, 100, step=1)] = 20,
        count: Annotated[int, Range(1, 5)] = 2,
    ):
        return studded(base(width), count)
    """
)

LABELED_MODEL = textwrap.dedent(
    """
    from typing import Annotated
    from build123d import Box, Compound, Pos
    from fastbuild import App, Range

    app = App()

    def slab(width: float):
        return Box(width, 8, 2)

    def knob(size: float):
        return Box(size, size, size)

    @app.part()
    def assembly(
        width: Annotated[float, Range(10, 100, step=1)] = 20,
        size: Annotated[float, Range(1, 5, step=1)] = 2,
    ):
        base = slab(width)
        base.label = "base"
        top = Pos(0, 0, 6) * knob(size)
        top.label = "top"
        return Compound(children=[base, top])
    """
)

MIXED_TAG_MODEL = LABELED_MODEL.replace(
    "top = Pos(0, 0, 6) * knob(size)",
    "top = Pos(0, 0, 6) * Box(size, size, size)",
)

GUARDS_MODEL = textwrap.dedent(
    """
    from typing import Annotated
    from build123d import Box
    from fastbuild import App, Range, no_cache

    app = App()

    @no_cache
    def leaf(width: float):
        return Box(width, 1, 1)

    def parent(width: float):
        return leaf(width)

    def noisy(width: float):
        reader = input
        return Box(width, 1, 1)

    def sneaky(width: float):
        maker = Box
        return maker(width, 1, 1)

    def exploding(width: float):
        raise ValueError("boom")

    @app.part()
    def thing(width: Annotated[float, Range(1, 50, step=1)] = 5):
        return Box(width, 2, 2)
    """
)


def install(tmp_path: Path, source: str, store: Store | None = None):
    path = tmp_path / "model.py"
    path.write_text(source)
    loaded = load_model(path)
    assert isinstance(loaded, LoadedModel)
    store = store if store is not None else Store(promote_threshold_s=0.0)
    return install_cache(loaded, store), store


def fp(obj: object) -> str:
    encoded = encode_arg(obj)
    assert not isinstance(encoded, Error)
    return fingerprint(encoded)


def test_primitive_encodings_are_distinct_by_type_and_value():
    assert fp(2) == fp(2)
    assert fp(1) != fp(2)
    assert fp(1) != fp(1.0)
    assert fp(1) != fp("1")
    assert fp(True) != fp(1)
    assert fp(None) != fp("None")


def test_container_encodings_are_type_tagged_and_dict_order_stable():
    assert fp((1, 2)) != fp([1, 2])
    assert fp({"a": 1, "b": 2}) == fp({"b": 2, "a": 1})
    assert fp([(1, 2)]) != fp([[1, 2]])


def test_unencodable_and_cyclic_arguments_return_errors():
    assert isinstance(encode_arg(object()), Error)
    cyclic: list = []
    cyclic.append(cyclic)
    assert isinstance(encode_arg(cyclic), Error)


def test_frozen_dataclasses_encode_nested_values_and_all_fields():
    @dataclass(frozen=True, slots=True)
    class Settings:
        width: float

    @dataclass(frozen=True, slots=True)
    class Base:
        settings: Settings

    @dataclass(frozen=True, slots=True)
    class Dimensions(Base):
        holes: int = field(compare=False)
        offsets: tuple = field(default=(1, 2), init=False)

        @property
        def derived(self):
            raise AssertionError("encoding must not evaluate properties")

    first = Dimensions(Settings(127.0), 5)
    same = Dimensions(Settings(127.0), 5)
    assert first is not same
    assert fp(first) == fp(same)
    assert fp(first) != fp(Dimensions(Settings(128.0), 5))
    assert first == Dimensions(Settings(127.0), 6)
    assert fp(first) != fp(Dimensions(Settings(127.0), 6))
    changed = Dimensions(Settings(127.0), 5)
    object.__setattr__(changed, "offsets", (3, 4))
    assert fp(first) != fp(changed)
    assert fp(first) != fp(
        {"settings": {"width": 127.0}, "holes": 5, "offsets": (1, 2)}
    )


def test_frozen_dataclass_keys_include_module_type_and_field_names():
    def record(name, module, field_name):
        cls = type(name, (), {
            "__module__": module, "__annotations__": {field_name: int},
        })
        return dataclass(frozen=True)(cls)(1)

    assert len({fp(record(*args)) for args in (
        ("Dimensions", "one", "width"),
        ("Dimensions", "two", "width"),
        ("Settings", "one", "width"),
        ("Dimensions", "one", "height"),
    )}) == 4


def test_dataclass_encoding_rejects_classes_mutable_and_unsupported_fields():
    @dataclass
    class Mutable:
        width: int = 1

    @dataclass(frozen=True)
    class Frozen:
        settings: object

    for value in (Mutable(), Mutable, Frozen, Frozen(object()), Frozen(Mutable())):
        assert isinstance(encode_arg(value), Error)
    cyclic = []
    value = Frozen(cyclic)
    cyclic.append(value)
    assert isinstance(encode_arg(value), Error)


def test_frozen_dataclass_arguments_reuse_geometry(tmp_path):
    source = textwrap.dedent("""
        from dataclasses import dataclass
        from build123d import Box
        from fastbuild import App

        app = App()

        @dataclass(frozen=True)
        class Dimensions:
            width: float

        def slab(dimensions):
            return Box(dimensions.width, 8, 2)

        @app.part()
        def widget(width: float = 20):
            return slab(Dimensions(width))
    """)
    installed, store = install(tmp_path, source)
    slab = installed.namespace["slab"]
    dimensions = installed.namespace["Dimensions"]
    first = slab(dimensions(20))
    same = slab(dimensions(20))
    changed = slab(dimensions(30))
    reverted = slab(dimensions(20))
    assert first._fb_key == same._fb_key == reverted._fb_key
    assert first._fb_key != changed._fb_key
    assert (store.stats().hits, store.stats().misses) == (2, 2)


def test_call_styles_and_omitted_defaults_share_one_key(tmp_path):
    installed, _ = install(tmp_path, CACHE_MODEL)
    ns = installed.namespace
    positional = ns["widget"](20, 2)._fb_key
    by_keyword = ns["widget"](count=2, width=20)._fb_key
    defaulted = ns["widget"]()._fb_key
    assert isinstance(positional, CacheKey)
    assert positional == by_keyword == defaulted


def test_entry_point_is_wrapped_and_plain_load_is_not(tmp_path):
    installed, _ = install(tmp_path, CACHE_MODEL)
    wrapped_fn = installed.parts["widget"].fn
    assert getattr(wrapped_fn, "_fb_wrapper", False)
    raw = load_model(tmp_path / "model.py")
    assert isinstance(raw, LoadedModel)
    assert not hasattr(raw.parts["widget"].fn, "_fb_wrapper")


def test_provenance_chain_hits_misses_and_reverts(tmp_path):
    installed, store = install(tmp_path, CACHE_MODEL)
    ns = installed.namespace

    b1 = ns["base"](20.0)
    assert isinstance(getattr(b1, "_fb_key", None), CacheKey)
    ns["studded"](b1, 2)
    cold = store.stats()
    assert (cold.hits, cold.misses) == (0, 4)

    b2 = ns["base"](20.0)
    assert b2._fb_key == b1._fb_key
    ns["studded"](b2, 2)
    warm = store.stats()
    assert (warm.hits, warm.misses) == (2, 4)

    ns["studded"](ns["base"](25.0), 2)
    changed = store.stats()
    assert changed.misses == 6
    assert changed.hits == 4

    ns["studded"](ns["base"](20.0), 2)
    reverted = store.stats()
    assert (reverted.hits, reverted.misses) == (6, 6)


def test_threshold_gates_storage_never_tagging(tmp_path):
    installed, store = install(
        tmp_path, CACHE_MODEL, store=Store(promote_threshold_s=999.0)
    )
    ns = installed.namespace
    b1 = ns["base"](20.0)
    b2 = ns["base"](20.0)
    stats = store.stats()
    assert stats.entries == 0 and stats.misses == 2
    assert b1._fb_key == b2._fb_key


def test_editing_a_helper_invalidates_every_function_and_revert_reuses(tmp_path):
    store = Store(promote_threshold_s=0.0)
    installed, _ = install(tmp_path, CACHE_MODEL, store=store)
    ns = installed.namespace
    ns["studded"](ns["base"](20.0), 2)
    baseline = store.stats()

    edited = CACHE_MODEL.replace("2.0 + (i % 2)", "3.0 + (i % 2)")
    reinstalled, _ = install(tmp_path, edited, store=store)
    ns2 = reinstalled.namespace
    body = ns2["base"](20.0)
    ns2["studded"](body, 2)
    after_edit = store.stats()
    assert after_edit.hits == baseline.hits
    assert after_edit.misses > baseline.misses

    restored, _ = install(tmp_path, CACHE_MODEL, store=store)
    ns3 = restored.namespace
    ns3["studded"](ns3["base"](20.0), 2)
    after_revert = store.stats()
    assert after_revert.hits == after_edit.hits + 2
    assert after_revert.misses == after_edit.misses


def test_guard_bypasses_never_tag_and_contagion_reaches_callers(tmp_path):
    installed, store = install(tmp_path, GUARDS_MODEL)
    ns = installed.namespace
    results = [ns["sneaky"](5.0), ns["noisy"](5.0), ns["leaf"](5.0), ns["parent"](5.0)]
    for result in results:
        assert not hasattr(result, "_fb_key")
    stats = store.stats()
    assert stats.bypasses == 5
    assert stats.hits == 0 and stats.misses == 0 and stats.entries == 0


def test_builder_context_bypasses_and_warns_once(tmp_path, caplog):
    from build123d import BuildPart

    installed, store = install(tmp_path, CACHE_MODEL)
    ns = installed.namespace
    with caplog.at_level(logging.WARNING, logger="fastbuild"):
        with BuildPart():
            first = ns["base"](12.0)
            second = ns["base"](12.0)
    assert store.stats().bypasses == 2
    assert not hasattr(first, "_fb_key") and not hasattr(second, "_fb_key")
    warnings = [r for r in caplog.records if "builder" in r.getMessage()]
    assert len(warnings) == 1


def test_failures_are_never_cached_and_upstream_entries_survive(tmp_path):
    installed, store = install(tmp_path, GUARDS_MODEL)
    ns = installed.namespace
    thing = installed.parts["thing"].fn
    thing(6.0)
    entries_before = store.stats().entries
    with pytest.raises(ValueError):
        ns["exploding"](1.0)
    with pytest.raises(ValueError):
        ns["exploding"](1.0)
    stats = store.stats()
    assert stats.entries == entries_before
    thing(6.0)
    assert store.stats().hits == 1


def test_store_side_poisoning_mutating_a_hit_copy_leaves_the_store_clean(tmp_path):
    installed, store = install(tmp_path, CACHE_MODEL)
    ns = installed.namespace
    first = ns["base"](30.0)
    volume = first.volume
    first.wrapped = None
    fresh = ns["base"](30.0)
    assert fresh.wrapped is not None
    assert abs(fresh.volume - volume) < 1e-9


@pytest.mark.xfail(
    strict=True,
    reason="SPEC-caching §3 Guards contract limit: in-place CAD mutation of a "
    "stage result misattributes provenance (data for the §6 content-hash fallback)",
)
def test_tag_side_poisoning_mutated_hit_copy_misattributes_provenance(tmp_path):
    installed, _ = install(tmp_path, CACHE_MODEL)
    ns = installed.namespace
    ns["studded"](ns["base"](20.0), 2)
    hit_copy = ns["base"](20.0)
    hit_copy.wrapped = ns["base"](77.0).wrapped
    poisoned = ns["studded"](hit_copy, 2)
    assert poisoned.bounding_box().size.X > 70


def test_joints_survive_deepcopy():
    from build123d import Box, Location, RigidJoint

    body = Box(4, 4, 4)
    RigidJoint("lid", body, Location())
    duplicate = copy.deepcopy(body)
    assert "lid" in duplicate.joints
    assert duplicate.joints["lid"].parent is duplicate


@pytest.mark.xfail(
    strict=False,
    reason="data for the disk-persistence punt (SPEC-caching §6): pickle "
    "round-trip of joints is not yet required to pass",
)
def test_joints_survive_pickle_roundtrip():
    from build123d import Box, Location, RigidJoint

    body = Box(4, 4, 4)
    RigidJoint("lid", body, Location())
    restored = pickle.loads(pickle.dumps(body))
    assert "lid" in restored.joints


def _ocp_group(root, name: str):
    return to_ocpgroup(root, names=[name])[0]


def test_provenance_ids_key_every_labeled_child(tmp_path):
    """SPEC-mesh-caching laws 5.1/5.5: each labeled, tagged child maps to its own
    instance under its own provenance digest, namespaced with `fb:`."""
    installed, _ = install(tmp_path, LABELED_MODEL)
    part = installed.parts["assembly"]
    root = part.fn(**{spec.name: default_value(spec) for spec in part.params})
    ids = _provenance_ids(root, _ocp_group(root, "assembly"))

    assert len(ids) == len(root.children) == 2
    assert {ref for ref, _ in ids} == {0, 1}
    assert all(cache_id.startswith(FB_CACHE_PREFIX) for _, cache_id in ids)
    by_ref = dict(ids)
    for index, child in enumerate(root.children):
        assert by_ref[index] == FB_CACHE_PREFIX + child._fb_key.digest


def test_provenance_ids_decline_anything_but_a_flat_labeled_compound(tmp_path):
    """Law 5.2: no exact correspondence, no substitution — never a partial one."""
    installed, _ = install(tmp_path, LABELED_MODEL)
    part = installed.parts["assembly"]
    root = part.fn(**{spec.name: default_value(spec) for spec in part.params})
    group = _ocp_group(root, "assembly")

    bare = Box(2, 2, 2)
    assert _provenance_ids(bare, _ocp_group(bare, "bare")) == ()

    group.objects[0].name = "renamed"
    assert _provenance_ids(root, group) == ()


def test_untagged_children_get_no_override(tmp_path):
    """Law 5.3: a child no wrapper produced carries no key, so it is skipped —
    its siblings still map."""
    installed, _ = install(tmp_path, MIXED_TAG_MODEL)
    part = installed.parts["assembly"]
    root = part.fn(**{spec.name: default_value(spec) for spec in part.params})
    ids = _provenance_ids(root, _ocp_group(root, "assembly"))

    assert len(root.children) == 2
    assert [getattr(c, "_fb_key", None) is None for c in root.children] == [False, True]
    assert len(ids) == 1
    assert ids[0][0] == 0


def test_cache_served_children_do_not_re_mesh(tmp_path):
    """The point of the whole spec: rebuilding with unchanged parameters adds no
    new tessellation entries, because provenance digests survive the store's copy
    where ocp_tessellate's content hash does not."""
    installed, _ = install(tmp_path, LABELED_MODEL)
    part = installed.parts["assembly"]
    values = {spec.name: default_value(spec) for spec in part.params}
    assert isinstance(build_part(part, values), BuildOutput)
    before = len(mesh_cache)
    assert isinstance(build_part(part, values), BuildOutput)
    assert len(mesh_cache) == before


def test_exact_revisit_recomputes_nothing(tmp_path):
    """SPEC-mesh-caching §3.5: the root mesh cache is gone, so a revisit re-runs
    the build — but every stage hits, and the scene metadata is unchanged."""
    installed, store = install(tmp_path, CACHE_MODEL)
    part = installed.parts["widget"]
    values = {spec.name: default_value(spec) for spec in part.params}
    first = build_part(part, values)
    assert isinstance(first, BuildOutput)
    before = store.stats()
    revisit = build_part(part, values)
    assert isinstance(revisit, BuildOutput)
    after = store.stats()
    assert after.misses == before.misses
    assert after.hits > before.hits
    assert revisit.shapes["shapes"] == first.shapes["shapes"]


def test_hit_is_much_cheaper_than_miss(tmp_path):
    chunky = CACHE_MODEL.replace(
        "from build123d import Box, Compound, Pos",
        "from build123d import Box, Compound, Pos, fillet",
    ).replace(
        "return Box(width, 8, 4)",
        "return fillet(Box(width, 8, 4).edges(), 1.0)",
    )
    installed, _ = install(tmp_path, chunky)
    ns = installed.namespace
    started = perf_counter()
    ns["base"](30.0)
    miss_s = perf_counter() - started
    started = perf_counter()
    ns["base"](30.0)
    hit_s = perf_counter() - started
    assert hit_s < miss_s / 5


def test_closure_hashes_cover_transitive_callees_and_cycles_terminate():
    chain = "def a():\n    return b()\n\ndef b():\n    return c()\n\ndef c():\n    return 1\n"
    edited = chain.replace("return 1", "return 2")
    before = resolve(analyze_functions(chain, function_source_hashes(chain)))
    after = resolve(analyze_functions(edited, function_source_hashes(edited)))
    assert before["a"].closure_hash != after["a"].closure_hash
    assert before["b"].closure_hash != after["b"].closure_hash
    assert before["c"].closure_hash != after["c"].closure_hash

    assert before["a"].members == ("a", "b", "c")
    assert before["c"].members == ("c",)

    cyclic = "def f():\n    return g()\n\ndef g():\n    return f()\n"
    quality = resolve(analyze_functions(cyclic, function_source_hashes(cyclic)))
    assert quality["f"].cacheable and quality["g"].cacheable
    assert quality["f"].closure_hash and quality["g"].closure_hash


def _stats(misses: int = 0, bypasses: int = 0) -> FrozenStats:
    return FrozenStats(hits=0, misses=misses, bypasses=bypasses, entries=0)


def test_cache_summary_hit_rate_clamps_and_tolerates_missing_stages():
    clean = cache_summary(_stats(), _stats(misses=1), stages=4)
    assert clean["hit_rate"] == 0.75 and clean["rebuilt"] == 1 and clean["stages"] == 4

    loopy = cache_summary(_stats(), _stats(misses=9, bypasses=2), stages=4)
    assert loopy["hit_rate"] == 0.0 and loopy["rebuilt"] == 11

    unknown = cache_summary(_stats(), _stats(misses=1), stages=None)
    assert unknown["hit_rate"] is None and unknown["stages"] is None


def test_build_ok_cache_payload_shape_encodes():
    msg = BuildOk(
        timings={"build_ms": 1.0, "tessellate_ms": 2.0},
        cache={"hits": 1, "misses": 2, "entries": 3, "last": "hit"},
    )
    assert encode(msg)["cache"] == {"hits": 1, "misses": 2, "entries": 3, "last": "hit"}


class FakeClient:
    def __init__(self):
        self.messages = []

    async def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    def of_type(self, msg_type: str):
        return [m for m in self.messages if m["type"] == msg_type]


async def _set_width(server, client, value):
    await server._handle_message(
        client,
        {
            "v": 1,
            "type": "set_param",
            "part": "widget",
            "name": "width",
            "value": value,
            "final": True,
        },
    )
    await server.queue.join()


async def test_build_status_carries_cache_stats_and_revisit_reads_as_hit(tmp_path):
    path = tmp_path / "model.py"
    path.write_text(CACHE_MODEL)
    server = ViewerServer(path, drag_debounce_s=0.02)
    server.store = Store(promote_threshold_s=0.0)
    await server.startup(watch=False)
    await server.queue.join()
    client = FakeClient()
    server.clients.add(client)

    await _set_width(server, client, 30)
    ok = [m for m in client.of_type("build_status") if m["state"] == "ok"][-1]
    assert set(ok["cache"]) == {
        "hits", "misses", "entries", "last", "stages", "rebuilt", "hit_rate",
    }
    assert ok["cache"]["last"] == "miss"
    assert ok["cache"]["stages"] == 4
    assert ok["cache"]["rebuilt"] > 0
    assert ok["cache"]["hit_rate"] < 1.0

    await _set_width(server, client, 25)
    await _set_width(server, client, 30)
    revisit = [m for m in client.of_type("build_status") if m["state"] == "ok"][-1]
    assert revisit["cache"]["last"] == "hit"
    assert revisit["cache"]["rebuilt"] == 0
    assert revisit["cache"]["hit_rate"] == 1.0
    await server.shutdown()


SLOW_MODEL = textwrap.dedent(
    """
    from typing import Annotated
    from time import sleep as pause
    from build123d import Box
    from fastbuild import App, Range

    app = App()

    @app.part()
    def slug(width: Annotated[float, Range(10, 100, step=1)] = 20):
        pause(0.25)
        return Box(width, 5, 5)
    """
)


async def test_reload_suppresses_inflight_build_broadcast(tmp_path):
    path = tmp_path / "model.py"
    path.write_text(SLOW_MODEL)
    server = ViewerServer(path, drag_debounce_s=0.02)
    await server.startup(watch=False)
    await server.queue.join()
    client = FakeClient()
    server.clients.add(client)

    await server._handle_message(
        client,
        {"v": 1, "type": "set_param", "part": "slug", "name": "width", "value": 30, "final": True},
    )
    await asyncio.sleep(0.05)
    await server.reload()
    await server.queue.join()

    update_indexes = [
        i for i, m in enumerate(client.messages) if m["type"] == "model_update"
    ]
    init_indexes = [i for i, m in enumerate(client.messages) if m["type"] == "init"]
    assert update_indexes and init_indexes
    assert len(update_indexes) == 1
    assert update_indexes[0] > init_indexes[0]
    assert server.state is not None and "slug" in server.state.parts
    await server.shutdown()
