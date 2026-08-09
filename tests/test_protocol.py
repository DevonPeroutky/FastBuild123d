import json
from pathlib import Path
from typing import Annotated

from fastbuild import App, Group, Range
from fastbuild.errors import Error
from fastbuild.protocol import (
    BuildError,
    BuildOk,
    Building,
    ModelUpdate,
    SelectPart,
    SetParam,
    encode,
    parse_client,
)
from fastbuild.session import init_message, initial_state, set_value

GOLDEN = Path(__file__).parent / "golden"


def _box_app() -> App:
    app = App()

    # vented is declared before height but grouped, so the golden file locks
    # the panel_order policy (ungrouped first), not just the group key.
    @app.part()
    def box(
        width: Annotated[float, Range(10, 100, step=1, unit="mm")] = 40,
        vented: Annotated[bool, Group("Options")] = False,
        height: float = 30.0,
    ):
        ...

    return app


def test_init_matches_golden_file():
    state = initial_state(_box_app().parts)
    for name, value in (("width", 42), ("vented", True)):
        state = set_value(state, "box", name, value)
        assert not isinstance(state, Error)
    assert encode(init_message(state)) == json.loads((GOLDEN / "init.json").read_text())


def test_model_update_matches_golden_file():
    golden = json.loads((GOLDEN / "model_update.json").read_text())
    msg = ModelUpdate(part="box", shapes=golden["shapes"])
    assert encode(msg) == golden


def test_build_status_variants_match_golden_file():
    building, ok, error = json.loads((GOLDEN / "build_status.json").read_text())
    assert encode(Building()) == building
    assert encode(BuildOk(timings={"build_ms": 53.7, "tessellate_ms": 12.9})) == ok
    assert encode(BuildError(error="Traceback (most recent call last): ...")) == error


def test_every_encoded_message_carries_the_envelope_version():
    state = initial_state(_box_app().parts)
    messages = [
        init_message(state),
        ModelUpdate(part="box", shapes={}),
        Building(),
        BuildOk(timings={}),
        BuildError(error="boom"),
    ]
    for msg in messages:
        assert encode(msg)["v"] == 1


def test_parse_client_round_trip_and_rejections():
    msg = parse_client(
        {"v": 1, "type": "set_param", "part": "box", "name": "width", "value": 42, "final": True}
    )
    assert msg == SetParam(part="box", name="width", value=42, final=True)

    assert parse_client({"v": 1, "type": "select_part", "part": "box"}) == SelectPart(part="box")

    assert isinstance(parse_client({"v": 2, "type": "select_part", "part": "box"}), Error)
    assert isinstance(parse_client({"v": 1, "type": "nope"}), Error)
    assert isinstance(parse_client({"v": 1, "type": "set_param", "part": "box"}), Error)
    assert isinstance(parse_client("not an object"), Error)
