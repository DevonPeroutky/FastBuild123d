import importlib
import os
import sys
import textwrap
from pathlib import Path

import pytest

from fastbuild.cache.store import Store
from fastbuild.errors import Error
from fastbuild.harness import LoadedModel, install_cache, load_model
from fastbuild.server import ViewerServer
from fastbuild.sources import is_model_source, read_sources, source_root


PROPERTY_MODEL = textwrap.dedent("""
    from dataclasses import dataclass
    from build123d import Box
    from fastbuild import App

    app = App()
    WALL = 4

    @dataclass(frozen=True)
    class Dimensions:
        width: float

        @property
        def keyboard_width(self):
            return self.width - WALL

    settings = Dimensions(20)

    def keyboard(dimensions):
        return Box(dimensions.keyboard_width, 2, 1)

    @app.part()
    def assembly():
        return keyboard(settings)
""")


def installed(path, store):
    loaded = load_model(path)
    assert isinstance(loaded, LoadedModel), loaded
    return install_cache(loaded, store)


@pytest.mark.parametrize("old,new", [
    ("self.width - WALL", "self.width - WALL - 2"),
    ("WALL = 4", "WALL = 6"),
])
def test_property_and_global_edits_invalidate_cached_callers(tmp_path, old, new):
    path = tmp_path / "model.py"
    path.write_text(PROPERTY_MODEL)
    store = Store(promote_threshold_s=0)
    before = installed(path, store)
    first = before.parts["assembly"].fn()
    before.parts["assembly"].fn()
    assert store.stats().hits == 1
    path.write_text(PROPERTY_MODEL.replace(old, new))
    after = installed(path, store)
    changed = after.parts["assembly"].fn()
    assert first.bounding_box().size.X == pytest.approx(16)
    assert changed.bounding_box().size.X == pytest.approx(14)
    assert first._fb_key != changed._fb_key
    assert (store.stats().hits, store.stats().misses) == (1, 4)


@pytest.fixture
def helper_package(tmp_path):
    package = tmp_path / "fb_test_sources"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "values.py").write_text("WIDTH = 20\n")
    (package / "dimensions.py").write_text(textwrap.dedent("""
        from dataclasses import dataclass
        from .values import WIDTH

        @dataclass(frozen=True)
        class Dimensions:
            @property
            def width(self):
                return WIDTH
    """))
    yield package
    for name in tuple(sys.modules):
        if name == "fb_test_sources" or name.startswith("fb_test_sources."):
            del sys.modules[name]


@pytest.mark.parametrize("imports,body", [
    ("from fb_test_sources.dimensions import Dimensions", "return Box(Dimensions().width, 2, 1)"),
    ("import fb_test_sources.dimensions as dims", "return Box(dims.Dimensions().width, 2, 1)"),
    ("", "import fb_test_sources.dimensions as dims\n    return Box(dims.Dimensions().width, 2, 1)"),
])
def test_transitive_helper_edits_reload_source_despite_stale_bytecode(
    tmp_path, helper_package, monkeypatch, imports, body,
):
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.import_module("fb_test_sources.dimensions")
    values = helper_package / "values.py"
    stamp = values.stat()
    path = tmp_path / "model.py"
    path.write_text(
        "from fastbuild import App\nfrom build123d import Box\n"
        f"{imports}\napp = App()\n"
        f"def keyboard():\n    {body}\n"
        "@app.part()\ndef assembly():\n    return keyboard()\n"
    )
    store = Store(promote_threshold_s=0)
    first = installed(path, store).parts["assembly"].fn()
    unchanged = installed(path, store).parts["assembly"].fn()
    if imports:
        assert first._fb_key == unchanged._fb_key
    else:
        assert not hasattr(first, "_fb_key")
    assert store.stats().hits == (1 if imports else 0)
    values.write_text("WIDTH = 30\n")
    os.utime(values, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    changed = installed(path, store).parts["assembly"].fn()
    assert first.bounding_box().size.X == pytest.approx(20)
    assert changed.bounding_box().size.X == pytest.approx(30)
    if imports:
        assert changed._fb_key != first._fb_key
    else:
        assert not hasattr(changed, "_fb_key")
    assert store.stats().hits == (1 if imports else 0)


def test_failed_helper_reload_restores_previous_modules(tmp_path, helper_package):
    path = tmp_path / "model.py"
    path.write_text(PROPERTY_MODEL + "\nfrom fb_test_sources.values import WIDTH\n")
    before = load_model(path)
    assert isinstance(before, LoadedModel)
    previous = sys.modules["fb_test_sources.values"]
    (helper_package / "values.py").write_text("raise ValueError('broken edit')\n")
    assert isinstance(load_model(path), Error)
    assert sys.modules["fb_test_sources.values"] is previous
    (helper_package / "values.py").write_text("WIDTH = 30\n")
    repaired = load_model(path)
    assert isinstance(repaired, LoadedModel)
    assert repaired.namespace["WIDTH"] == 30


def test_package_models_support_relative_imports(helper_package):
    path = helper_package / "model.py"
    path.write_text(PROPERTY_MODEL + "\nfrom .values import WIDTH\n")
    loaded = load_model(path)
    assert isinstance(loaded, LoadedModel)
    assert loaded.namespace["WIDTH"] == 20
    assert source_root(path) == helper_package


def test_source_revision_tracks_additions_deletions_and_model_identity(tmp_path):
    path = tmp_path / "model.py"
    path.write_text(PROPERTY_MODEL)
    other = tmp_path / "other.py"
    other.write_text(PROPERTY_MODEL.replace("WALL = 4", "WALL = 6"))
    initial = read_sources(path).digest
    assert initial != read_sources(other).digest
    extra = tmp_path / "unused.py"
    extra.write_text("VALUE = 1\n")
    assert read_sources(path).digest != initial
    extra.unlink()
    assert read_sources(path).digest == initial
    hidden = tmp_path / ".venv"
    hidden.mkdir()
    (hidden / "ignored.py").write_text("ignored")
    assert read_sources(path).digest == initial
    assert not is_model_source(tmp_path, hidden / "ignored.py")
    assert is_model_source(tmp_path, extra)


async def test_helper_file_watch_event_reloads_model(tmp_path, helper_package, monkeypatch):
    path = tmp_path / "model.py"
    path.write_text(PROPERTY_MODEL + "\nfrom fb_test_sources.values import WIDTH\n")
    server = ViewerServer(path)
    await server.startup(watch=False)
    await server.queue.join()
    values = helper_package / "values.py"
    values.write_text("WIDTH = 30\n")

    async def changes(root, *, watch_filter, **kwargs):
        assert root == tmp_path
        assert watch_filter(2, str(values))
        assert not watch_filter(2, str(tmp_path / "image.png"))
        yield {(2, str(values))}

    monkeypatch.setattr("fastbuild.server.awatch", changes)
    try:
        await server._watch()
        await server.queue.join()
        assert sys.modules["fb_test_sources.values"].WIDTH == 30
    finally:
        await server.shutdown()
