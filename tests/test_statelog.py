from typing import Annotated

from fastbuild import App, Range
from fastbuild.session import initial_state, migrate, select_part, set_value
from fastbuild.statelog import format_transition, state_diff, state_lines


def _app() -> App:
    app = App()

    @app.part()
    def box(width: Annotated[float, Range(10, 100, step=1)] = 40):
        return width

    @app.part()
    def lid(height: Annotated[float, Range(1, 20, step=1)] = 5):
        return height

    return app


def test_state_lines_renders_selected_and_one_line_per_part():
    state = initial_state(_app())
    assert state_lines(state) == (
        "selected='box'",
        "box  width=40",
        "lid  height=5",
    )


def test_state_lines_pads_part_names_to_common_width():
    app = App()

    @app.part()
    def box(width: Annotated[float, Range(10, 100, step=1)] = 40):
        return width

    @app.part()
    def enclosure(depth: Annotated[float, Range(1, 50, step=1)] = 10):
        return depth

    assert state_lines(initial_state(app)) == (
        "selected='box'",
        "box        width=40",
        "enclosure  depth=10",
    )


def test_diff_initial():
    assert state_diff(None, initial_state(_app())) == ("(initial state)",)


def test_diff_value_change():
    prev = initial_state(_app())
    new = set_value(prev, "box", "width", 60)
    assert state_diff(prev, new) == ("box.width: 40 -> 60",)


def test_diff_selection_change():
    prev = initial_state(_app())
    new = select_part(prev, "lid")
    assert state_diff(prev, new) == ("selected: 'box' -> 'lid'",)


def test_diff_no_change():
    state = initial_state(_app())
    assert state_diff(state, state) == ("(no change)",)


def test_diff_migration_reports_added_and_removed_parts():
    prev = initial_state(_app())

    grown = App()

    @grown.part()
    def box(width: Annotated[float, Range(10, 100, step=1)] = 40):
        return width

    @grown.part()
    def base(depth: Annotated[float, Range(1, 50, step=1)] = 10):
        return depth

    new = migrate(prev, grown)
    assert state_diff(prev, new) == ("-part 'lid'", "+part 'base'")


def test_format_transition_layout():
    prev = initial_state(_app())
    new = set_value(prev, "box", "width", 60)
    text = format_transition("set_param box.width=60 final=True", prev, new)
    assert text.splitlines() == [
        "",
        "── set_param box.width=60 final=True ───────────────────────",
        "  diff   box.width: 40 -> 60",
        "  state  selected='box'",
        "         box  width=60",
        "         lid  height=5",
    ]


def test_format_transition_initial_has_header_and_state():
    new = initial_state(_app())
    text = format_transition("startup", None, new)
    assert text.splitlines() == [
        "",
        "── startup ─────────────────────────────────────────────────",
        "  diff   (initial state)",
        "  state  selected='box'",
        "         box  width=40",
        "         lid  height=5",
    ]
