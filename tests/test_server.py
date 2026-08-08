import asyncio
import json
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fastbuild.server import ViewerServer, create_app

FAST_MODEL = textwrap.dedent(
    """
    from typing import Annotated
    from build123d import Box
    from fastbuild import App, Range

    app = App()

    @app.part()
    def box(width: Annotated[float, Range(10, 100, step=1)] = 40):
        return Box(width, 10, 10)
    """
)

RAISING_MODEL = FAST_MODEL.replace("return Box(width, 10, 10)", 'raise ValueError("bad geometry")')

MIGRATED_MODEL = textwrap.dedent(
    """
    from typing import Annotated
    from build123d import Box
    from fastbuild import App, Range

    app = App()

    @app.part()
    def box(
        width: Annotated[float, Range(10, 100, step=1)] = 40,
        tall: bool = False,
    ):
        return Box(width, 10, 30 if tall else 10)
    """
)


class FakeClient:
    def __init__(self):
        self.messages = []

    async def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    def of_type(self, msg_type: str):
        return [m for m in self.messages if m["type"] == msg_type]


@pytest.fixture
def model_file(tmp_path: Path) -> Path:
    path = tmp_path / "model.py"
    path.write_text(FAST_MODEL)
    return path


async def _started_server(model_file: Path) -> tuple[ViewerServer, FakeClient]:
    server = ViewerServer(model_file, drag_debounce_s=0.02)
    await server.startup(watch=False)
    await server.queue.join()
    client = FakeClient()
    server.clients.add(client)
    return server, client


async def test_final_set_param_builds_and_pushes_model_update(model_file):
    server, client = await _started_server(model_file)
    await server._handle_message(client, {
        "v": 1, "type": "set_param", "part": "box", "name": "width", "value": 60, "final": True,
    })
    await server.queue.join()
    updates = client.of_type("model_update")
    assert len(updates) == 1
    assert updates[0]["part"] == "box"
    assert updates[0]["shapes"]["shapes"]["parts"]
    statuses = [m["state"] for m in client.of_type("build_status")]
    assert statuses == ["building", "ok"]
    assert server.state.values["box"]["width"] == 60


async def test_raising_model_keeps_last_good_scene_server_side(model_file):
    server, client = await _started_server(model_file)
    last_good_before = server.last_good["box"]

    model_file.write_text(RAISING_MODEL)
    await server.reload()
    await server.queue.join()

    assert server.last_good["box"] is last_good_before
    error_statuses = [m for m in client.of_type("build_status") if m["state"] == "error"]
    assert "bad geometry" in error_statuses[-1]["error"]

    model_file.write_text(FAST_MODEL)
    await server.reload()
    await server.queue.join()
    assert client.of_type("build_status")[-1]["state"] == "ok"


async def test_syntax_error_on_save_keeps_old_params_and_recovers(model_file):
    server, client = await _started_server(model_file)
    model_file.write_text("def broken(:\n")
    await server.reload()
    assert "SyntaxError" in client.of_type("build_status")[-1]["error"]
    assert server.state is not None
    assert "box" in server.state.parts

    model_file.write_text(FAST_MODEL)
    await server.reload()
    await server.queue.join()
    assert client.of_type("build_status")[-1]["state"] == "ok"


async def test_signature_migration_keeps_surviving_values(model_file):
    server, client = await _started_server(model_file)
    await server._handle_message(client, {
        "v": 1, "type": "set_param", "part": "box", "name": "width", "value": 77, "final": True,
    })
    await server.queue.join()

    model_file.write_text(MIGRATED_MODEL)
    await server.reload()
    await server.queue.join()

    init = client.of_type("init")[-1]
    params = {p["name"]: p for p in init["parts"][0]["params"]}
    assert params["width"]["value"] == 77
    assert params["tall"]["value"] is False
    assert set(params) == {"width", "tall"}


async def test_non_final_drags_coalesce_through_debounce(model_file):
    server, client = await _started_server(model_file)
    for value in (41, 42, 43, 44):
        await server._handle_message(client, {
            "v": 1, "type": "set_param", "part": "box", "name": "width", "value": value, "final": False,
        })
    await asyncio.sleep(0.05)
    await server.queue.join()
    updates = client.of_type("model_update")
    assert len(updates) == 1
    assert server.state.values["box"]["width"] == 44


def test_page_and_websocket_serve_init_and_initial_model(model_file):
    app = create_app(model_file, watch=False)
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "cad_view" in page.text
        with client.websocket_connect("/ws") as ws:
            init = ws.receive_json()
            assert init["v"] == 1 and init["type"] == "init"
            assert init["parts"][0]["name"] == "box"
