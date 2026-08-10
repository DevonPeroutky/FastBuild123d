# FastBuild123

Turn any [build123d](https://github.com/gumyr/build123d) model into a live, interactive
parametric web UI. Annotate your part function's parameters with a FastAPI-style
`@app.part()` decorator and get auto-generated sliders in the browser with instant
3D preview — the type-annotated signature *is* the UI definition.

## Quick start

Run a bundled example without installing anything:

```bash
uvx fastbuild123 demo enclosure
```

Or install it into your own project:

```bash
uv add fastbuild123      # or: pip install fastbuild123
fastbuild view my_model.py
```

A browser page opens with parameter controls on the left and a live 3D view on the
right. Drag a slider or save the file — the model updates in place, keeping your
camera pose, tree state, and clipping planes. A model that raises keeps the last-good
geometry on screen with an error banner; fix the file and it recovers.

## Defining parts

```python
from typing import Annotated, Literal
from build123d import Part, Box
from fastbuild import App, Group, Range

app = App()

@app.part()
def enclosure(
    width:  Annotated[float, Range(20, 200, step=1, unit="mm")] = 80,
    wall:   Annotated[float, Range(0.8, 5, step=0.1, unit="mm")] = 2,
    ribs:   Annotated[int, Range(0, 8), Group("Ribs")] = 4,
    vented: bool = False,
    style:  Literal["rounded", "chamfered"] = "rounded",
) -> Part:
    ...
```

Widget mapping: `float`/`int` + `Range` → slider, bare numbers → number input,
`bool` → checkbox, `Literal`/`Enum` → dropdown. Every parameter needs a default —
defaults are the initial build. Multiple `@app.part()` functions in one file get a
part selector. Add `Group("Label")` to a parameter's annotation to place it under
a named collapsible section in the panel; ungrouped parameters render first.

## Headless export

```bash
fastbuild export my_model.py enclosure --param width=120 -o enclosure.step
```

Values are validated against each parameter's `Range`; output format follows the
extension (`.step`/`.stp`/`.stl`).

## Requirements

Python 3.10–3.13 on macOS (Apple Silicon or Intel), Linux (glibc 2.31+, x86-64 or
aarch64), or 64-bit Windows — wherever `cadquery-ocp` publishes wheels. Expect a
~500 MB install; the OpenCASCADE kernel is most of it. Alpine/musl is not supported.

## Development

```bash
uv sync
uv run pytest
uv run fastbuild demo enclosure
```

Examples live in `fastbuild/examples/` so they ship with the package and stay
runnable via `fastbuild demo`. Specs live in `specs/`. The 3D component is a
vendored, pinned build of
[three-cad-viewer](https://github.com/bernhard-42/three-cad-viewer)
(`fastbuild/static/vendor/`, see `VERSION` there) fed by
[ocp-tessellate](https://github.com/bernhard-42/ocp-tessellate) output over a
versioned websocket protocol.

## License

MIT — see [LICENSE](LICENSE). Bundles three-cad-viewer (MIT), whose license is
included at `fastbuild/static/vendor/LICENSE-three-cad-viewer`.
