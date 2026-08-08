import enum
from typing import Annotated, Literal

import pytest

from fastbuild.errors import Error
from fastbuild.params import (
    Checkbox,
    Dropdown,
    NumberInput,
    ParamError,
    Range,
    Slider,
    Unsupported,
    coerce,
    introspect,
    quantize,
    schema,
    to_python,
)


class Finish(enum.Enum):
    RAW = enum.auto()
    ANODIZED = enum.auto()


def test_each_annotation_form_maps_to_expected_widget():
    def part(
        width: Annotated[float, Range(20, 200, step=1, unit="mm")] = 80,
        count: Annotated[int, Range(0, 8)] = 4,
        scale: float = 1.5,
        vented: bool = False,
        style: Literal["rounded", "chamfered"] = "rounded",
        finish: Finish = Finish.RAW,
        weird: list = [],
    ):
        ...

    specs = {s.name: s for s in introspect(part)}
    assert specs["width"] == Slider(
        name="width", min=20, max=200, step=1, integer=False, default=80, unit="mm"
    )
    assert specs["count"].step == 1  # int slider default step
    assert isinstance(specs["count"], Slider) and specs["count"].integer
    assert specs["scale"] == NumberInput(name="scale", integer=False, default=1.5)
    assert specs["vented"] == Checkbox(name="vented", default=False)
    assert specs["style"] == Dropdown(
        name="style", options=("rounded", "chamfered"), default="rounded"
    )
    assert specs["finish"].options == ("RAW", "ANODIZED")
    assert specs["finish"].default == "RAW"
    assert isinstance(specs["weird"], Unsupported)


def test_float_slider_default_step_is_hundredth_of_range():
    def part(r: Annotated[float, Range(0, 50)] = 10):
        ...

    (spec,) = introspect(part)
    assert spec.step == 0.5


def test_missing_default_is_a_decoration_error():
    def part(width: float):
        ...

    with pytest.raises(ParamError, match="no default"):
        introspect(part)


def test_unsupported_type_is_read_only_not_a_crash():
    def part(anything: dict = {"a": 1}):
        ...

    (spec,) = introspect(part)
    assert isinstance(spec, Unsupported)
    assert schema(spec, spec.default_repr)["kind"] == "readonly"
    assert isinstance(coerce(spec, {"a": 2}), Error)


def test_quantize_snaps_to_step_grid_and_clamps():
    spec = Slider(name="w", min=0.8, max=5, step=0.1, integer=False, default=2)
    assert quantize(spec, 2.3400000001) == 2.3
    assert quantize(spec, -10) == 0.8
    assert quantize(spec, 99) == 5
    # idempotency: quantized values are fixed points (stable cache keys)
    for raw in (0.85, 1.234, 4.999):
        once = quantize(spec, raw)
        assert quantize(spec, once) == once


def test_coerce_rejects_wrong_types_and_unknown_options():
    slider = Slider(name="w", min=0, max=10, step=1, integer=True, default=5)
    assert isinstance(coerce(slider, "abc"), Error)
    assert isinstance(coerce(slider, True), Error)
    dropdown = Dropdown(name="s", options=("a", "b"), default="a")
    assert isinstance(coerce(dropdown, "c"), Error)
    checkbox = Checkbox(name="v", default=False)
    assert isinstance(coerce(checkbox, 1), Error)


def test_enum_dropdown_round_trips_to_member():
    dropdown = Dropdown(
        name="finish", options=("RAW", "ANODIZED"), default="RAW", enum_class=Finish
    )
    value = coerce(dropdown, "ANODIZED")
    assert not isinstance(value, Error)
    assert to_python(dropdown, value) is Finish.ANODIZED
