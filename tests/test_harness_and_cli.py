import textwrap
from pathlib import Path

import pytest

from fastbuild.cli import main, resolve_export_values
from fastbuild.errors import Error
from fastbuild.harness import LoadedModel, function_source_hashes, load_model

MODEL = textwrap.dedent(
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


@pytest.fixture
def model_file(tmp_path: Path) -> Path:
    path = tmp_path / "model.py"
    path.write_text(MODEL)
    return path


def test_load_model_registers_parts(model_file):
    loaded = load_model(model_file)
    assert isinstance(loaded, LoadedModel)
    assert list(loaded.app.parts) == ["box"]
    assert "box" in loaded.function_hashes


def test_load_errors_are_returned_not_raised(tmp_path):
    missing = load_model(tmp_path / "nope.py")
    assert isinstance(missing, Error)

    bad = tmp_path / "bad.py"
    bad.write_text("1/0")
    bad_result = load_model(bad)
    assert isinstance(bad_result, Error)
    assert "ZeroDivisionError" in bad_result.message

    empty = tmp_path / "empty.py"
    empty.write_text("x = 1")
    empty_result = load_model(empty)
    assert isinstance(empty_result, Error)
    assert "no fastbuild App" in empty_result.message


def test_editing_a_function_changes_only_its_hash():
    before = function_source_hashes("def a():\n    return 1\n\ndef b():\n    return 2\n")
    after = function_source_hashes("def a():\n    return 111\n\ndef b():\n    return 2\n")
    assert before["a"] != after["a"]
    assert before["b"] == after["b"]


NESTED = textwrap.dedent(
    """
    def outer():
        def helper():
            return 1
        return helper

    class Shell:
        def helper(self):
            return 2

    def helper():
        return 3
    """
)


def test_same_named_functions_hash_under_distinct_qualnames():
    hashes = function_source_hashes(NESTED)
    assert set(hashes) == {"outer", "outer.<locals>.helper", "Shell.helper", "helper"}
    assert len({hashes["outer.<locals>.helper"], hashes["Shell.helper"], hashes["helper"]}) == 3


def test_editing_a_nested_helper_does_not_touch_its_namesakes():
    edited = function_source_hashes(NESTED.replace("return 1", "return 11"))
    before = function_source_hashes(NESTED)
    assert edited["outer.<locals>.helper"] != before["outer.<locals>.helper"]
    assert edited["Shell.helper"] == before["Shell.helper"]
    assert edited["helper"] == before["helper"]


def test_export_values_validate_range_strictly(model_file):
    loaded = load_model(model_file)
    assert isinstance(loaded, LoadedModel)
    part = loaded.app.parts["box"]

    assert resolve_export_values(part, ["width=50"]) == {"width": 50.0}

    too_big = resolve_export_values(part, ["width=500"])
    assert isinstance(too_big, Error)
    assert "outside Range" in too_big.message

    unknown = resolve_export_values(part, ["nope=1"])
    assert isinstance(unknown, Error)
    assert "no parameter" in unknown.message


def test_export_cli_writes_stl(model_file, tmp_path, capsys):
    out = tmp_path / "box.stl"
    main(["export", str(model_file), "box", "--param", "width=20", "-o", str(out)])
    assert out.exists() and out.stat().st_size > 0


def test_export_cli_rejects_unknown_part(model_file, tmp_path):
    with pytest.raises(SystemExit):
        main(["export", str(model_file), "nope", "-o", str(tmp_path / "x.stl")])
