"""Cache subsystem tests (SPEC-caching §5): key laws, tagging laws, wiring,
staleness, poisoning, guards, contagion, mesh revisits, and server behavior."""

import asyncio
import copy
import json
import logging
import pickle
import textwrap
from pathlib import Path
from time import perf_counter

import pytest

from fastbuild.builder import BuildOutput, build_part
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


def test_call_styles_and_omitted_defaults_share_one_key(tmp_path):
    installed, _ = install(tmp_path, CACHE_MODEL)
    ns = installed.namespace
    key_fn = installed.parts["widget"].fn.call_key
    assert key_fn({"width": 20, "count": 2}) == key_fn({})
    positional = ns["widget"](20, 2)._fb_key
    by_keyword = ns["widget"](count=2, width=20)._fb_key
    assert positional == by_keyword == key_fn({})


def test_entry_point_is_wrapped_and_plain_load_is_not(tmp_path):
    installed, _ = install(tmp_path, CACHE_MODEL)
    wrapped_fn = installed.parts["widget"].fn
    assert getattr(wrapped_fn, "_fb_wrapper", False)
    assert isinstance(wrapped_fn.call_key({}), CacheKey)
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


def test_editing_a_comprehension_helper_invalidates_dependents(tmp_path):
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
    assert after_edit.hits == baseline.hits + 1
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


def test_exact_revisit_hits_mesh_and_skips_build_entirely(tmp_path):
    installed, store = install(tmp_path, CACHE_MODEL)
    part = installed.parts["widget"]
    values = {spec.name: default_value(spec) for spec in part.params}
    first = build_part(part, values, store=store)
    assert isinstance(first, BuildOutput)
    revisit = build_part(part, values, store=store)
    assert isinstance(revisit, BuildOutput)
    assert revisit.timings == {"build_ms": 0.0, "tessellate_ms": 0.0}
    assert revisit.shapes is first.shapes
    assert store.stats().mesh_hits == 1


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
    return FrozenStats(hits=0, misses=misses, bypasses=bypasses, mesh_hits=0, entries=0)


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


async def test_build_status_carries_cache_stats_and_mesh_hits_read_as_hits(tmp_path):
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


async def test_reload_during_inflight_build_lands_after_its_broadcast(tmp_path):
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
    assert update_indexes[0] < init_indexes[0]
    assert server.state is not None and "slug" in server.state.parts
    await server.shutdown()
