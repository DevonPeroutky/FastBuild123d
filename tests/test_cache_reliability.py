import asyncio
import sys
import json
from fastapi import WebSocketDisconnect
from pathlib import Path
from threading import Event

import pytest
from hypothesis import given, strategies as st

from fastbuild.buildloop import build_identity
from fastbuild.cache.graph import function_identity
from fastbuild.cache.runtime import CacheRuntime
from fastbuild.cache.store import Store
from fastbuild.harness import LoadedModel, install_cache, load_model
from fastbuild.server import ViewerServer
from fastbuild.protocol import BuildOk


@given(st.dictionaries(st.text(), st.one_of(st.integers(), st.text(), st.booleans())))
def test_build_identity_canonicalizes_parameter_order(values):
    assert build_identity('rev', 'part', values) == build_identity('rev', 'part', dict(reversed(list(values.items()))))
    assert build_identity('rev', 'part', values) != build_identity('new', 'part', values)


@given(st.text(alphabet='abcdefghijklmnopqrstuvwxyz', min_size=1), st.text(alphabet='abcdefghijklmnopqrstuvwxyz', min_size=1))
def test_function_identity_separates_paths_and_names(a, b):
    assert function_identity(Path(a + '.py'), b) != function_identity(Path(a + '_other.py'), b)
    assert function_identity(Path(a + '.py'), b) != function_identity(Path(a + '.py'), b + '_other')


def test_runtime_nested_exception_and_independence():
    first, second = CacheRuntime(), CacheRuntime()
    with first.disabled():
        assert first.is_disabled and not second.is_disabled
        with pytest.raises(ValueError), first.disabled():
            raise ValueError()
        assert first.is_disabled
    assert not first.is_disabled


@pytest.fixture(autouse=True)
def clean_helper_modules():
    previous = {name: sys.modules.pop(name) for name in ('helper', 'bridge', 'other') if name in sys.modules}
    yield
    for name in ('helper', 'bridge', 'other'):
        sys.modules.pop(name, None)
    sys.modules.update(previous)


def install(path, store):
    loaded = load_model(path)
    assert isinstance(loaded, LoadedModel), loaded
    return install_cache(loaded, store)


@pytest.mark.parametrize('import_line,call', [
    ('from helper import stage as alias', 'alias(2)'),
    ('import helper as h', 'h.stage(2)'),
    ('from bridge import stage', 'stage(2)'),
    ('from .helper import stage', 'stage(2)'),
])
def test_imported_stages_reuse_and_revert(tmp_path, import_line, call):
    if import_line.startswith('from .'):
        (tmp_path / '__init__.py').write_text('')
    helper = tmp_path / 'helper.py'
    original = 'from build123d import Box\ndef stage(n):\n    return Box(n, 1, 1)\n'
    helper.write_text(original)
    (tmp_path / 'bridge.py').write_text('from helper import stage\n')
    path = tmp_path / 'model.py'
    path.write_text(f'from fastbuild import App\n{import_line}\napp=App()\n@app.part()\ndef part(n: int=1):\n    return {call}\n')
    store = Store(promote_threshold_s=0)
    loaded = install(path, store)
    first = loaded.parts['part'].fn(1)
    loaded.parts['part'].fn(2)
    assert store.stats().hits == 1
    helper.write_text(original.replace('Box(n,', 'Box(n+1,'))
    changed = install(path, store).parts['part'].fn(1)
    assert changed.bounding_box().size.X != first.bounding_box().size.X
    helper.write_text(original)
    install(path, store).parts['part'].fn(1)
    assert store.stats().hits == 2


@pytest.mark.parametrize('mode', ['disabled', 'optout', 'runtime', 'dynamic'])
def test_bypass_policies_across_modules(tmp_path, mode):
    (tmp_path / 'helper.py').write_text('from build123d import Box\nfrom fastbuild import no_cache\n' + ('@no_cache\n' if mode == 'optout' else '') + 'def stage():\n    return Box(1,1,1)\n')
    path = tmp_path / 'model.py'
    body = {'runtime': '    import helper\n', 'dynamic': '    __import__("helper")\n'}.get(mode, '')
    path.write_text('from fastbuild import App\nfrom helper import stage\napp=App()\n'
                    + ('@app.part(cache=False)\n' if mode == 'disabled' else '@app.part()\n')
                    + 'def part():\n' + body + '    return stage()\n')
    store = Store(promote_threshold_s=0)
    loaded = install(path, store)
    for _ in range(2):
        result = loaded.parts['part'].fn()
        assert not hasattr(result, '_fb_key')
    assert store.stats().hits == 0
    if mode != 'optout':
        loaded.namespace['stage']()
        loaded.namespace['stage']()
        assert store.stats().hits == 1


MODEL = 'from fastbuild import App\nfrom build123d import Box\napp=App()\n@app.part()\ndef a(n: int=1):\n    return Box(n,1,1)\n@app.part()\ndef b():\n    return Box(2,1,1)\n'


async def select(server, part):
    await server._handle_message(None, {'v': 1, 'type': 'select_part', 'part': part})
    await server.queue.join()


async def test_inactive_edit_and_deleted_recreated_part(tmp_path):
    path = tmp_path / 'model.py'
    path.write_text(MODEL)
    server = ViewerServer(path)
    try:
        await server.startup(False)
        await server.queue.join()
        await select(server, 'b')
        old = server.last_good['b']
        await select(server, 'a')
        path.write_text(MODEL.replace('Box(2,', 'Box(3,'))
        await server.reload()
        await server.queue.join()
        await select(server, 'b')
        assert server.last_good['b'].identity != old.identity
        path.write_text(MODEL[:MODEL.index('@app.part()\ndef b')])
        await server.reload()
        await server.queue.join()
        assert 'b' not in server.last_good
        path.write_text(MODEL)
        await server.reload()
        await server.queue.join()
        await select(server, 'b')
        assert isinstance(server.last_status, BuildOk)
    finally:
        await server.shutdown()


@pytest.mark.parametrize('change', ['selection', 'parameter', 'reload'])
async def test_inflight_output_is_superseded(tmp_path, monkeypatch, change):
    path = tmp_path / 'model.py'
    path.write_text(MODEL)
    server = ViewerServer(path)
    entered, release = Event(), Event()
    original = server._build
    def blocked(request):
        entered.set()
        assert release.wait(5)
        return original(request)
    monkeypatch.setattr(server, '_build', blocked)
    try:
        await server.startup(False)
        await asyncio.to_thread(entered.wait, 5)
        if change == 'selection':
            await server._handle_message(None, {'v': 1, 'type': 'select_part', 'part': 'b'})
        elif change == 'parameter':
            await server._handle_message(None, {'v': 1, 'type': 'set_param', 'part': 'a', 'name': 'n', 'value': 3, 'final': True})
        else:
            path.write_text(MODEL.replace('Box(n,', 'Box(n+1,'))
            reload = asyncio.create_task(server.reload())
            await asyncio.sleep(0)
        release.set()
        if change == 'reload':
            await reload
        await server.queue.join()
        assert all(scene.identity == server.current_identity() for scene in server.last_good.values())
    finally:
        release.set()
        await server.shutdown()


class ReconnectingClient:
    def __init__(self):
        self.messages = []

    async def accept(self):
        pass

    async def send_text(self, text):
        self.messages.append(json.loads(text))

    async def receive_json(self):
        raise WebSocketDisconnect()


async def test_reconnect_pairs_scene_and_status_and_failed_build(tmp_path):
    path = tmp_path / 'model.py'
    path.write_text(MODEL)
    server = ViewerServer(path)
    try:
        await server.startup(False)
        await server.queue.join()
        await select(server, 'b')
        await select(server, 'a')
        client = ReconnectingClient()
        await server.handle_client(client)
        assert client.messages[1]['part'] == 'a'
        assert client.messages[2]['state'] == 'ok'
        old = server.last_good['a']
        path.write_text(MODEL.replace('return Box(n,1,1)', 'raise ValueError("bad")'))
        await server.reload()
        await server.queue.join()
        client = ReconnectingClient()
        await server.handle_client(client)
        assert server.last_good['a'] is old
        assert client.messages[1]['part'] == 'a'
        assert client.messages[2]['state'] == 'error'
        await select(server, 'a')
        assert server.last_good['a'] is old
        assert not isinstance(server.last_status, BuildOk)
    finally:
        await server.shutdown()


async def test_disabled_selection_rebuilds_shared_helper(tmp_path):
    path = tmp_path / 'model.py'
    path.write_text('from fastbuild import App\nfrom build123d import Box\napp=App()\ndef stage():\n    return Box(1,1,1)\n@app.part(cache=False)\ndef disabled():\n    return stage()\n@app.part()\ndef enabled():\n    return stage()\n')
    server = ViewerServer(path)
    server.store = Store(promote_threshold_s=0)
    try:
        await server.startup(False)
        await server.queue.join()
        old = server.last_good['disabled']
        await select(server, 'disabled')
        assert server.last_good['disabled'] is not old
        assert server.store.stats().bypasses == 4
        await select(server, 'enabled')
        hits = server.store.stats().hits
        await select(server, 'disabled')
        assert server.store.stats().hits == hits
        server.state.parts['enabled'].fn()
        assert server.store.stats().hits == hits + 1
    finally:
        await server.shutdown()


def test_same_named_helpers_have_distinct_geometry_keys(tmp_path):
    for name, width in [('helper', 1), ('other', 2)]:
        (tmp_path / f'{name}.py').write_text(f'from build123d import Box\ndef stage():\n    return Box({width},1,1)\n')
    path = tmp_path / 'model.py'
    path.write_text('from fastbuild import App\nimport helper, other\napp=App()\n@app.part()\ndef part():\n    return helper.stage() + other.stage()\n')
    store = Store(promote_threshold_s=0)
    loaded = install(path, store)
    first = loaded.namespace['helper'].stage()
    second = loaded.namespace['other'].stage()
    assert first._fb_key != second._fb_key
    assert first.volume != second.volume


def test_unused_sources_are_not_imported_and_plain_load_is_unwrapped(tmp_path):
    (tmp_path / 'unused.py').write_text('raise AssertionError("must not import")\n')
    path = tmp_path / 'model.py'
    path.write_text(MODEL)
    loaded = load_model(path)
    assert isinstance(loaded, LoadedModel)
    assert set(loaded.namespaces) == {path}
    assert not hasattr(loaded.parts['a'].fn, '_fb_wrapper')
    installed = install_cache(loaded, Store())
    assert installed.parts['a'].fn._fb_wrapper


def test_disabled_exception_restores_shared_helper_caching(tmp_path):
    path = tmp_path / 'model.py'
    path.write_text('from fastbuild import App\nfrom build123d import Box\napp=App()\ndef stage():\n    return Box(1,1,1)\n@app.part(cache=False)\ndef part():\n    stage()\n    raise ValueError("boom")\n')
    store = Store(promote_threshold_s=0)
    loaded = install(path, store)
    with pytest.raises(ValueError):
        loaded.parts['part'].fn()
    assert store.stats().entries == 0
    loaded.namespace['stage']()
    loaded.namespace['stage']()
    assert store.stats().hits == 1
